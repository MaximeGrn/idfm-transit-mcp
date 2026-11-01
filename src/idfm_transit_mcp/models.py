"""Stable result envelope; provider payloads are normalized by the tools."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TransitError(Exception):
    def __init__(self, code: str, message: str, retry_after: float | None = None):
        self.code, self.message, self.retry_after = code, message, retry_after
        super().__init__(message)


class Result(BaseModel):
    status: Literal["ok", "empty", "ambiguous", "unavailable", "partial"] = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    source: str = "Île-de-France Mobilités — PRIM"
    source_url: str = "https://prim.iledefrance-mobilites.fr/"
    data_licence_url: str = "https://cloud.fabmob.io/s/eYWWJBdM3fQiFNm"
    data_licence_note: str = (
        "PRIM product terms apply. The SIRI catalogue also references ODbL; "
        "see product metadata before redistribution."
    )
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    timezone: str = "Europe/Paris"


def failure(exc: TransitError) -> Result:
    return Result(
        status="unavailable",
        error={
            "code": exc.code,
            "message": exc.message,
            "retry_after_seconds": exc.retry_after,
        },
    )
