"""Market-data & analytics schemas."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class TickerOut(BaseModel):
    symbol: str
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    high: Decimal | None
    low: Decimal | None
    base_volume: Decimal | None
    timestamp: int | None


class OrderBookLevelOut(BaseModel):
    price: Decimal
    amount: Decimal


class OrderBookOut(BaseModel):
    symbol: str
    bids: list[OrderBookLevelOut]
    asks: list[OrderBookLevelOut]
    timestamp: int | None
    spread: Decimal | None


class CandleOut(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorOut(BaseModel):
    symbol: str
    timeframe: str
    indicator: str
    values: list[float | None]
    latest: float | None
