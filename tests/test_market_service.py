"""Tests for MarketService's pure indicator dispatch and offline fetches."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.analytics.models import Candle
from app.core.exceptions import ValidationError
from app.services.market_service import MarketService
from tests.fakes import FakeFactory


def _candles(n=60):
    return [
        Candle(
            timestamp=1_700_000_000_000 + i * 3600_000,
            open=100 + i,
            high=100 + i + 1,
            low=100 + i - 1,
            close=100 + i,
            volume=10.0,
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("name", ["rsi", "sma", "ema", "atr", "vwap", "adx"])
def test_compute_indicator_dispatch(name):
    candles = _candles(120)
    values = MarketService.compute_indicator(name, candles, period=14)
    assert len(values) == len(candles)


def test_compute_indicator_unknown_raises():
    candles = _candles(20)
    with pytest.raises(ValidationError):
        MarketService.compute_indicator("nonsense", candles)


@pytest.mark.asyncio
async def test_service_ticker_and_candles_offline():
    service = MarketService(FakeFactory(last_price=Decimal(250)))
    ticker = await service.ticker("binance", "BTC/USDT")
    assert ticker.last == Decimal(250)
    candles = await service.candles("binance", "BTC/USDT", "1h", 30)
    assert len(candles) == 30


@pytest.mark.asyncio
async def test_service_indicator_offline():
    service = MarketService(FakeFactory(last_price=Decimal(100)))
    values = await service.indicator(
        exchange_id="binance", symbol="BTC/USDT", name="rsi", period=14, limit=100
    )
    assert len(values) == 100
