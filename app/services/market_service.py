"""Public market-data and technical-analysis service."""

from __future__ import annotations

from app.analytics import indicators
from app.analytics.models import Candle
from app.core.exceptions import ValidationError
from app.exchange.factory import ExchangeFactory
from app.exchange.models import OrderBook, Ticker

# Indicators reachable via the API. Multi-series indicators expose their primary
# line (MACD line, Bollinger middle band, Stochastic %K, SuperTrend line) so the
# single-series response contract holds.
SUPPORTED_INDICATORS = frozenset(
    {"rsi", "sma", "ema", "atr", "vwap", "adx", "macd", "bollinger", "stochastic", "supertrend"}
)

# Allow-list of candle timeframes accepted from clients, so an arbitrary string
# is never forwarded to the exchange.
SUPPORTED_TIMEFRAMES = frozenset(
    {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"}
)


def _validate_timeframe(timeframe: str) -> str:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValidationError(f"Unsupported timeframe: {timeframe}")
    return timeframe


class MarketService:
    def __init__(self, factory: ExchangeFactory) -> None:
        self._factory = factory

    async def ticker(self, exchange_id: str, symbol: str) -> Ticker:
        adapter = self._factory.public_market_adapter(exchange_id)
        try:
            return await adapter.fetch_ticker(symbol)
        finally:
            await adapter.close()

    async def order_book(self, exchange_id: str, symbol: str, limit: int = 50) -> OrderBook:
        adapter = self._factory.public_market_adapter(exchange_id)
        try:
            return await adapter.fetch_order_book(symbol, limit)
        finally:
            await adapter.close()

    async def candles(
        self, exchange_id: str, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[Candle]:
        _validate_timeframe(timeframe)
        adapter = self._factory.public_market_adapter(exchange_id)
        try:
            return await adapter.fetch_ohlcv(symbol, timeframe, limit)
        finally:
            await adapter.close()

    async def indicator(
        self,
        *,
        exchange_id: str,
        symbol: str,
        name: str,
        timeframe: str = "1h",
        period: int = 14,
        limit: int = 200,
    ) -> list[float | None]:
        name = name.lower()
        # Validate before spending a network round-trip on candles.
        if name not in SUPPORTED_INDICATORS:
            raise ValidationError(f"Unknown indicator: {name}")
        candles = await self.candles(exchange_id, symbol, timeframe, limit)
        return self.compute_indicator(name, candles, period)

    @staticmethod
    def compute_indicator(name: str, candles: list[Candle], period: int = 14) -> list[float | None]:
        """Compute a named indicator over candles (pure; unit-tested directly).

        Multi-series indicators return their primary line so the API's
        single-series contract holds.
        """
        name = name.lower()
        closes = [c.close for c in candles]
        if name == "rsi":
            return indicators.rsi(closes, period)
        if name == "sma":
            return indicators.sma(closes, period)
        if name == "ema":
            return indicators.ema(closes, period)
        if name == "atr":
            return indicators.atr(candles, period)
        if name == "vwap":
            return indicators.vwap(candles)
        if name == "adx":
            return indicators.adx(candles, period)
        if name == "macd":
            return indicators.macd(closes)[0]  # MACD line
        if name == "bollinger":
            return indicators.bollinger_bands(closes, period)[1]  # middle band
        if name == "stochastic":
            return indicators.stochastic(candles, period)[0]  # %K
        if name == "supertrend":
            return indicators.supertrend(candles, period)[0]  # trend line
        raise ValidationError(f"Unknown indicator: {name}")
