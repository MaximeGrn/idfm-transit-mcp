"""Configuration without implicit .env loading or secret logging."""

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    api_key: SecretStr = SecretStr("")
    timeout: float = Field(default=20, gt=0, le=120)
    retries: int = Field(default=2, ge=0, le=3)
    requests_per_second: float = Field(default=4, gt=0, le=5)
    max_retry_wait: float = Field(default=30, ge=0, le=120)
    max_pages: int = Field(default=20, ge=1, le=100)

    @classmethod
    def from_env(cls) -> "Settings":
        secret_file = os.getenv("IDFM_API_KEY_FILE")
        key = os.getenv("IDFM_API_KEY", "").strip()
        if secret_file:
            try:
                key = Path(secret_file).read_text(encoding="utf-8").strip()
            except OSError:
                raise ValueError(
                    "Cannot read IDFM_API_KEY_FILE; check path and permissions."
                ) from None
        return cls(api_key=SecretStr(key))
