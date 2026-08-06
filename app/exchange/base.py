"""Abstract exchange adapter — the port every concrete exchange plugs into.

This interface is deliberately narrow and normalised. Concrete adapters
(:class:`~app.exchange.ccxt_adapter.CcxtAdapter`,
:class:`~app.exchange.paper.PaperAdapter`) translate between it and a specific
exchange. Higher layers (services, API) depend only on this abstraction —
the Adapter + Dependency-Inversion pattern that makes new exchanges cheap to add.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from app.analytics.models import Candle
from app.exchange.models import (
    Balance,
    ExchangeStatus,
    Order,
    OrderBook,
    OrderRequest,
    Position,
    Ticker,
)


@runtime_checkable
class MarketDataSource(Protocol):
    """The read-only market-data surface (public endpoints, no credentials)."""

    exchange_id: str

    async def fetch_ticker(self, symbol: str) -> Ticker: ...

    async def fetch_order_book(self, symbol: str, limit: int = 50) -> OrderBook: ...

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[Candle]: ...


class ExchangeAdapter(ABC):
    """Full trading + account adapter for a single exchange account."""

    exchange_id: str

    # --- lifecycle ---------------------------------------------------------
    @abstractmethod
    async def close(self) -> None:
        """Release network resources (must be idempotent)."""

    async def __aenter__(self) -> ExchangeAdapter:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # --- health ------------------------------------------------------------
    @abstractmethod
    async def status(self) -> ExchangeStatus: ...

    # --- market data -------------------------------------------------------
    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    async def fetch_order_book(self, symbol: str, limit: int = 50) -> OrderBook: ...

    @abstractmethod
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[Candle]: ...

    # --- account -----------------------------------------------------------
    @abstractmethod
    async def fetch_balance(self) -> list[Balance]: ...

    @abstractmethod
    async def fetch_positions(self, symbols: list[str] | None = None) -> list[Position]: ...

    # --- trading -----------------------------------------------------------
    @abstractmethod
    async def create_order(self, request: OrderRequest) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> Order: ...

    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> Order: ...

    @abstractmethod
    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]: ...

    @property
    @abstractmethod
    def is_paper(self) -> bool:
        """Whether this adapter simulates trading rather than sending live orders."""
