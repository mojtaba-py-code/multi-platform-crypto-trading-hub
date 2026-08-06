"""Exchange-account schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.exchange.registry import SUPPORTED_EXCHANGES


class _BaseAccount(BaseModel):
    exchange_id: str = Field(examples=["binance"])
    # Safe charset only — labels are stored and later rendered in the UI.
    label: str = Field(default="default", max_length=64, pattern=r"^[\w .\-]{1,64}$")
    market_type: str = Field(default="spot", pattern="^(spot|margin|futures|swap)$")

    @field_validator("exchange_id")
    @classmethod
    def _known_exchange(cls, value: str) -> str:
        value = value.lower()
        if value not in SUPPORTED_EXCHANGES:
            raise ValueError(f"Unsupported exchange: {value}")
        return value


class PaperAccountCreate(_BaseAccount):
    """Create a paper (simulated) account — no credentials required."""


class AccountCreate(_BaseAccount):
    """Create a live account. Credentials are encrypted before storage.

    Note: whether live orders are actually sent still depends on the server's
    ``ALLOW_LIVE_TRADING`` policy — a live account may still be simulated.
    """

    api_key: str = Field(min_length=8, max_length=256, repr=False)
    api_secret: str = Field(min_length=8, max_length=512, repr=False)
    passphrase: str | None = Field(default=None, max_length=256, repr=False)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    exchange_id: str
    label: str
    market_type: str
    is_paper: bool
    is_active: bool
    created_at: datetime
    # Credentials are intentionally never serialised.
