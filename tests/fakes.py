"""Test doubles for exchange adapters (no network)."""

from __future__ import annotations

from decimal import Decimal

from app.analytics.models import Candle
from app.config.settings import Settings
from app.exchange.factory import ExchangeFactory
from app.exchange.models import OrderBook, OrderBookLevel, Ticker


class FakeMarketData:
    """A deterministic in-memory :class:`MarketDataSource`."""

    def __init__(self, exchange_id: str = "binance", last: Decimal = Decimal(100)) -> None:
        self.exchange_id = exchange_id
        self._last = last

    def set_price(self, price: Decimal | int | float) -> None:
        self._last = Decimal(str(price))

    async def fetch_ticker(self, symbol: str) -> Ticker:
        return Ticker(
            symbol=symbol,
            last=self._last,
            bid=self._last - 1,
            ask=self._last + 1,
            high=self._last + 5,
            low=self._last - 5,
            base_volume=Decimal(1000),
            timestamp=1_700_000_000_000,
        )

    async def fetch_order_book(self, symbol: str, limit: int = 50) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            bids=[OrderBookLevel(self._last - 1, Decimal(1))],
            asks=[OrderBookLevel(self._last + 1, Decimal(1))],
            timestamp=1_700_000_000_000,
        )

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[Candle]:
        base = float(self._last)
        return [
            Candle(
                timestamp=1_700_000_000_000 + i * 3600_000,
                open=base + i,
                high=base + i + 1,
                low=base + i - 1,
                close=base + i,
                volume=10.0,
            )
            for i in range(limit)
        ]

    async def close(self) -> None:
        return None


class FakeFactory(ExchangeFactory):
    """Factory whose public market data is deterministic and offline.

    Because :meth:`ExchangeFactory.build` wraps ``public_market_adapter`` in the
    paper adapter, overriding just this one method makes the whole paper-trading
    path network-free in tests.
    """

    def __init__(self, *, live: bool = False, last_price: Decimal = Decimal(100)) -> None:
        super().__init__(Settings(allow_live_trading=live, use_exchange_testnet=True))
        self._last_price = last_price

    def public_market_adapter(self, exchange_id: str) -> FakeMarketData:  # type: ignore[override]
        return FakeMarketData(exchange_id=exchange_id, last=self._last_price)
