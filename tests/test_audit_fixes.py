"""Regression tests for defects found in the professional audit.

Each test pins a specific bug that was fixed so it cannot silently return.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.analytics import indicators
from app.core.resilience import get_circuit_breaker, reset_circuit_breakers
from app.exchange.models import OrderRequest
from app.exchange.paper import PaperAdapter
from app.exchange.paper_store import PaperLedger
from app.trading.enums import OrderSide, OrderStatus, OrderType
from tests.fakes import FakeMarketData


# --- Paper ledger persistence (correctness BUG1 / arch #1,#3) ---------------
@pytest.mark.asyncio
async def test_paper_sell_after_buy_with_shared_ledger():
    ledger = PaperLedger(quote_asset="USDT", starting_balance=Decimal(100_000))
    market = FakeMarketData(last=Decimal(100))

    async def order(adapter, side, amount):
        return await adapter.create_order(
            OrderRequest(symbol="BTC/USDT", side=side, type=OrderType.market, amount=amount)
        )

    # Two *separate* adapter instances sharing one ledger (mimics per-request build).
    buy = await order(PaperAdapter(market, ledger=ledger), OrderSide.buy, Decimal(2))
    assert buy.status is OrderStatus.filled
    # The base balance from the buy must persist so a later sell succeeds.
    sell = await order(PaperAdapter(market, ledger=ledger), OrderSide.sell, Decimal(1))
    assert sell.status is OrderStatus.filled
    balances = {b.asset: b for b in ledger.balances.values()}
    assert balances["BTC"].free == Decimal(1)  # 2 bought - 1 sold


@pytest.mark.asyncio
async def test_paper_order_ids_are_unique():
    ledger = PaperLedger(starting_balance=Decimal(100_000))
    market = FakeMarketData(last=Decimal(100))
    ids = set()
    for _ in range(5):
        adapter = PaperAdapter(market, ledger=ledger)
        o = await adapter.create_order(
            OrderRequest(
                symbol="BTC/USDT", side=OrderSide.buy, type=OrderType.market, amount=Decimal("0.1")
            )
        )
        ids.add(o.id)
    assert len(ids) == 5  # no "paper-1" collisions


@pytest.mark.asyncio
async def test_paper_close_delegates_to_market():
    class ClosableMarket(FakeMarketData):
        closed = False

        async def close(self):
            ClosableMarket.closed = True

    adapter = PaperAdapter(ClosableMarket())
    await adapter.close()
    assert ClosableMarket.closed is True


# --- Marketable limit fills at market, not limit (correctness BUG3) ---------
@pytest.mark.asyncio
async def test_aggressive_buy_limit_fills_at_market_price():
    adapter = PaperAdapter(FakeMarketData(last=Decimal(100)), starting_balance=Decimal(100_000))
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.buy,
            type=OrderType.limit,
            amount=Decimal(1),
            price=Decimal(200),  # way above market
        )
    )
    assert order.status is OrderStatus.filled
    assert order.average == Decimal(100)  # not 200


@pytest.mark.asyncio
async def test_aggressive_sell_limit_fills_at_market_price():
    adapter = PaperAdapter(FakeMarketData(last=Decimal(100)), starting_balance=Decimal(100_000))
    # seed a base balance to sell
    await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT", side=OrderSide.buy, type=OrderType.market, amount=Decimal(1)
        )
    )
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.sell,
            type=OrderType.limit,
            amount=Decimal(1),
            price=Decimal(50),  # far below market
        )
    )
    assert order.average == Decimal(100)  # not 50


# --- pivot points S3 (correctness BUG2) ------------------------------------
def test_pivot_s3_formula():
    p = indicators.pivot_points(prev_high=110, prev_low=90, prev_close=100)
    assert p["pivot"] == pytest.approx(100.0)
    assert p["s3"] == pytest.approx(70.0)  # low - 2*(high - pivot) = 90 - 2*10
    assert p["r3"] == pytest.approx(130.0)


# --- ADX guard relaxed ------------------------------------------------------
def test_adx_available_at_minimum_length():
    import math

    from app.analytics.models import Candle

    period = 5
    candles = [
        Candle(i, 100 + math.sin(i), 101 + math.sin(i), 99 + math.sin(i), 100 + math.sin(i), 1.0)
        for i in range(2 * period)
    ]
    result = indicators.adx(candles, period)
    assert any(v is not None for v in result)


# --- Shared circuit breaker (async #4) -------------------------------------
def test_circuit_breaker_registry_is_shared():
    reset_circuit_breakers()
    a = get_circuit_breaker("ccxt:binance")
    b = get_circuit_breaker("ccxt:binance")
    assert a is b  # same instance → state persists across adapters
    c = get_circuit_breaker("ccxt:okx")
    assert c is not a
    reset_circuit_breakers()
