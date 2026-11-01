"""Async PRIM HTTP client, bounded retries, pacing and memory-only TTL cache."""

import asyncio
import copy
import json
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .config import Settings
from .models import TransitError

ROOT = "https://prim.iledefrance-mobilites.fr/marketplace"
NAVITIA = "/v2/navitia"
TRAFFIC = "/v2/navitia/line_reports"
FETCH_TIMES: ContextVar[list[str] | None] = ContextVar("fetch_times", default=None)


def no_data_payload(path: str, data: Any) -> dict[str, Any] | None:
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("id") if isinstance(error, dict) else None
    if path.endswith("/journeys") and code == "no_solution":
        return {"journeys": [], "provider_notice": code}
    if path.endswith("/departures") and code in (
        "no_departure_this_day",
        "no_active_circulation_this_day",
        "terminus",
        "partial_terminus",
        "active_disruption",
    ):
        return {"departures": [], "provider_notice": code}
    return None


def retry_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


class PrimClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=ROOT,
            timeout=settings.timeout,
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self._cache: dict[str, tuple[float, dict[str, Any], str]] = {}
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        self._cooldown = 0.0

    async def close(self) -> None:
        await self.http.aclose()

    async def get(
        self, path: str, params: dict[str, Any] | None = None, ttl: int = 0
    ) -> dict[str, Any]:
        if not path.startswith((NAVITIA + "/", "/stop-monitoring")) or ".." in path:
            raise ValueError("Unsupported PRIM path")
        key = path + str(sorted((params or {}).items()))
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            if (times := FETCH_TIMES.get()) is not None:
                times.append(cached[2])
            return copy.deepcopy(cached[1])
        secret = self.settings.api_key.get_secret_value()
        if not secret:
            raise TransitError("missing_api_key", "Set IDFM_API_KEY or IDFM_API_KEY_FILE.")
        for attempt in range(self.settings.retries + 1):
            async with self._lock:
                remaining = self._cooldown - time.monotonic()
                if remaining > self.settings.max_retry_wait:
                    raise TransitError(
                        "quota_cooldown", "PRIM retry cooldown is still active.", remaining
                    )
                await asyncio.sleep(max(0, self._next_request - time.monotonic()))
                self._next_request = time.monotonic() + 1 / self.settings.requests_per_second
            try:
                response = await self.http.get(
                    ROOT + path, params=params, headers={"apiKey": secret}
                )
            except httpx.TransportError:
                if attempt < self.settings.retries:
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                raise TransitError(
                    "network_error", "PRIM request timed out or could not connect."
                ) from None
            status = response.status_code
            empty = None
            if status == 404:
                try:
                    empty = no_data_payload(path, response.json())
                except ValueError:
                    pass
                if empty is not None:
                    status = 200
            delay = retry_seconds(response.headers.get("Retry-After"))
            if status == 429 or 500 <= status <= 599:
                wait = delay if delay is not None else 0.5 * 2**attempt
                async with self._lock:
                    self._cooldown = time.monotonic() + wait
                    self._next_request = max(self._next_request, self._cooldown)
                # A long Retry-After is reported, never shortened to retry prematurely.
                if attempt < self.settings.retries and wait <= self.settings.max_retry_wait:
                    continue
            if status >= 400 or 300 <= status < 400:
                code, message = {
                    401: ("authentication_failed", "PRIM rejected the API key."),
                    403: (
                        "access_denied",
                        "PRIM denied access; verify API entitlement and quotas.",
                    ),
                    404: ("not_found", "PRIM could not find this resource."),
                    429: ("quota_exceeded", "PRIM quota exceeded; wait before retrying."),
                }.get(status, ("upstream_error", f"PRIM returned HTTP {status}."))
                # Do not echo upstream bodies, URLs, headers, or exception representations.
                raise TransitError(code, message, delay)
            try:
                data = empty if empty is not None else response.json()
            except ValueError:
                raise TransitError("invalid_response", "PRIM returned non-JSON data.") from None
            if not isinstance(data, dict):
                raise TransitError(
                    "invalid_response", "PRIM returned an unexpected JSON structure."
                )
            if data.get("error"):
                known_empty = no_data_payload(path, data)
                if known_empty is None:
                    raise TransitError("provider_error", "PRIM reports an error for this request.")
                data = known_empty
            # Redact a secret even if a faulty provider echoes it in a successful payload.
            if secret in response.text:
                data = json.loads(json.dumps(data).replace(secret, "[REDACTED]"))
            fetched = datetime.now(UTC).isoformat()
            if (times := FETCH_TIMES.get()) is not None:
                times.append(fetched)
            if ttl:
                if len(self._cache) >= 256:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = (time.monotonic() + ttl, copy.deepcopy(data), fetched)
            return data
        raise AssertionError("Unreachable retry state")

    async def pages(
        self, path: str, collection: str, params: dict[str, Any] | None = None, ttl: int = 60
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        items, disruptions = [], {}
        for page in range(self.settings.max_pages):
            data = await self.get(path, {**(params or {}), "count": 100, "start_page": page}, ttl)
            if collection not in data or not isinstance(data[collection], list):
                raise TransitError("invalid_response", f"PRIM response lacks {collection}.")
            batch = data[collection]
            items.extend(batch)
            for disruption in data.get("disruptions", []):
                disruptions[disruption["id"]] = disruption
            pagination = data.get("pagination", {})
            total = pagination.get("total_result")
            if total is not None and len(items) >= total:
                return items, list(disruptions.values()), True
            if not pagination:
                # Missing pagination is not evidence that a network-wide report is complete.
                return items, list(disruptions.values()), False
            if not batch:
                return items, list(disruptions.values()), total is not None and len(items) >= total
        return items, list(disruptions.values()), False
