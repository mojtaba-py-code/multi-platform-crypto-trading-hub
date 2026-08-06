"""Exchange-domain data transfer objects.

Normalised representations that every adapter maps to, so the rest of the
application never depends on a specific exchange's JSON shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.trading.enums import (
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)


@dataclass(frozen=True, slots=True)
class Ticker:
    symbol: str
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    high: Decimal | None
    low: Decimal | None
    base_volume: Decimal | None
    timestamp: int | None


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    amount: Decimal


@dataclass(frozen=True, slots=True)
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: int | None

    @property
    def spread(self) -> Decimal | None:
        if self.bids and self.asks:
            return self.asks[0].price - self.bids[0].price
        return None


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    free: Decimal
    used: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.used


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A normalised request to place an order."""

    symbol: str
    side: OrderSide
    type: OrderType
    amount: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.gtc
    reduce_only: bool = False
    post_only: bool = False
    leverage: int | None = None
    client_order_id: str | None = None
    params: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    status: OrderStatus
    amount: Decimal
    filled: Decimal
    price: Decimal | None
    average: Decimal | None
    timestamp: int | None
    client_order_id: str | None = None
    fee: Decimal = Decimal(0)
    is_paper: bool = False

    @property
    def remaining(self) -> Decimal:
        return self.amount - self.filled


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    side: PositionSide
    contracts: Decimal
    entry_price: Decimal
    mark_price: Decimal | None
    leverage: int
    margin_mode: MarginMode
    unrealized_pnl: Decimal
    liquidation_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ExchangeStatus:
    exchange_id: str
    ok: bool
    latency_ms: float | None = None
    message: str | None = None
