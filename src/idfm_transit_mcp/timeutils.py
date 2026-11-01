"""Navitia uses local compact dates; SIRI timestamps include UTC offsets."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def parse_datetime(value: str) -> datetime:
    try:
        dt = (
            datetime.strptime(value, "%Y%m%dT%H%M%S")
            if len(value) == 15
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except ValueError:
        raise ValueError(
            "Use an ISO 8601 date and time, preferably with an explicit offset."
        ) from None
    if dt.tzinfo is None:
        first, second = dt.replace(tzinfo=PARIS, fold=0), dt.replace(tzinfo=PARIS, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError(
                "Ambiguous or nonexistent Paris clock time; provide an explicit offset."
            )
        dt = first
    return dt.astimezone(PARIS)


def navitia_datetime(value: str) -> str:
    # Navitia explicitly accepts UTC ISO dates. Preserve the instant across DST folds.
    return parse_datetime(value).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def output_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parse_datetime(value).isoformat()
    except ValueError:
        return value  # Preserve provider data rather than inventing an instant.


def now_paris() -> datetime:
    return datetime.now(UTC).astimezone(PARIS)
