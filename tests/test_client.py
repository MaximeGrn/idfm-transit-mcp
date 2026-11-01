import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from idfm_transit_mcp.client import NAVITIA, PrimClient, retry_seconds
from idfm_transit_mcp.config import Settings
from idfm_transit_mcp.models import TransitError


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "access_denied"),
        (404, "not_found"),
        (429, "quota_exceeded"),
        (500, "upstream_error"),
        (302, "upstream_error"),
    ],
)
async def test_safe_http_errors(status, code):
    async with httpx.AsyncClient():
        c = PrimClient(
            Settings(api_key="never-print-this", retries=0),
            httpx.MockTransport(lambda req: httpx.Response(status, text="never-print-this")),
        )
        with pytest.raises(TransitError) as exc:
            await c.get(NAVITIA + "/places")
        assert exc.value.code == code
        assert "never-print-this" not in str(exc.value)
        await c.close()


async def test_long_retry_after_is_not_shortened():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "120"})

    c = PrimClient(Settings(api_key="test", retries=2), httpx.MockTransport(handler))
    with pytest.raises(TransitError) as error:
        await c.get(NAVITIA + "/places")
    assert error.value.retry_after == 120
    with pytest.raises(TransitError, match="cooldown"):
        await c.get(NAVITIA + "/places")
    assert len(calls) == 1
    await c.close()


@pytest.mark.parametrize("status", [429, 503])
async def test_transient_retry_and_cache(status):
    calls = []

    def handler(req):
        calls.append(req)
        assert req.headers["apikey"] == "test-secret"
        return (
            httpx.Response(status, headers={"Retry-After": "0"})
            if len(calls) == 1
            else httpx.Response(200, json={"places": [{"name": "test-secret"}]})
        )

    c = PrimClient(Settings(api_key="test-secret"), httpx.MockTransport(handler))
    first = await c.get(NAVITIA + "/places", ttl=60)
    first["places"].clear()
    second = await c.get(NAVITIA + "/places", ttl=60)
    assert len(calls) == 2
    assert second["places"][0]["name"] == "[REDACTED]"
    await c.close()


async def test_network_timeout_is_bounded():
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("secret request", request=req)

    c = PrimClient(Settings(api_key="test", retries=1), httpx.MockTransport(handler))
    with pytest.raises(TransitError) as error:
        await c.get(NAVITIA + "/places")
    assert error.value.code == "network_error"
    assert len(calls) == 2
    assert "secret request" not in str(error.value)
    await c.close()


@pytest.mark.parametrize("payload", [[], "bad JSON", {"error": {"message": "private"}}])
async def test_invalid_payload(payload):
    def handler(req):
        return (
            httpx.Response(200, text=payload)
            if isinstance(payload, str)
            else httpx.Response(200, json=payload)
        )

    c = PrimClient(Settings(api_key="test"), httpx.MockTransport(handler))
    with pytest.raises(TransitError):
        await c.get(NAVITIA + "/places")
    await c.close()


async def test_missing_key_never_sends_request():
    def handler(req):
        pytest.fail("HTTP must not run without credentials")

    c = PrimClient(Settings(), httpx.MockTransport(handler))
    with pytest.raises(TransitError) as error:
        await c.get(NAVITIA + "/places")
    assert error.value.code == "missing_api_key"
    await c.close()


def test_retry_after_http_date():
    future = datetime.now(UTC) + timedelta(seconds=30)
    assert 28 <= retry_seconds(format_datetime(future)) <= 30
    assert retry_seconds("nonsense") is None


async def test_concurrent_requests_are_paced():
    loop = asyncio.get_running_loop()
    sent = []

    def handler(req):
        sent.append(loop.time())
        return httpx.Response(200, json={"places": []})

    c = PrimClient(Settings(api_key="test"), httpx.MockTransport(handler))
    await asyncio.gather(*(c.get(NAVITIA + "/places", {"q": str(i)}) for i in range(3)))
    assert all(b - a >= 0.23 for a, b in zip(sent, sent[1:], strict=False))
    await c.close()


@pytest.mark.parametrize(
    "status,path,error_id,collection",
    [
        (404, "/journeys", "no_solution", "journeys"),
        (200, "/journeys", "no_solution", "journeys"),
        (404, "/stop_areas/stop_area:1/departures", "no_departure_this_day", "departures"),
    ],
)
async def test_documented_no_data_errors_are_empty(status, path, error_id, collection):
    c = PrimClient(
        Settings(api_key="test"),
        httpx.MockTransport(lambda req: httpx.Response(status, json={"error": {"id": error_id}})),
    )
    result = await c.get(NAVITIA + path)
    assert result[collection] == [] and result["provider_notice"] == error_id
    await c.close()
