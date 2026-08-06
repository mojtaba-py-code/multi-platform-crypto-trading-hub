"""Tests for the paper-trading adapter's fill model and ledger."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.exceptions import InsufficientBalanceError, NotFoundError
from app.exchange.models import OrderRequest
from app.exchange.paper import PaperAdapter
from app.trading.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from tests.fakes import FakeMarketData


def _adapter(price=100, balance=100_000) -> PaperAdapter:
    return PaperAdapter(FakeMarketData(last=Decimal(price)), starting_balance=Decimal(balance))


@pytest.mark.asyncio
async def test_market_buy_fills_and_updates_balance():
    adapter = _adapter(price=100, balance=10_000)
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT", side=OrderSide.buy, type=OrderType.market, amount=Decimal(1)
        )
    )
    assert order.status is OrderStatus.filled
    assert order.average == Decimal(100)
    assert order.is_paper is True
    balances = {b.asset: b for b in await adapter.fetch_balance()}
    assert balances["BTC"].free == Decimal(1)
    # 10_000 - 100 (cost) - fee
    assert balances["USDT"].free < Decimal(9900)


@pytest.mark.asyncio
async def test_market_buy_insufficient_balance():
    adapter = _adapter(price=100, balance=50)
    with pytest.raises(InsufficientBalanceError):
        await adapter.create_order(
            OrderRequest(
                symbol="BTC/USDT", side=OrderSide.buy, type=OrderType.market, amount=Decimal(1)
            )
        )


@pytest.mark.asyncio
async def test_limit_buy_below_market_rests():
    adapter = _adapter(price=100)
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.buy,
            type=OrderType.limit,
            amount=Decimal(1),
            price=Decimal(90),  # below market -> not marketable
            time_in_force=TimeInForce.gtc,
        )
    )
    assert order.status is OrderStatus.open
    open_orders = await adapter.fetch_open_orders()
    assert len(open_orders) == 1


@pytest.mark.asyncio
async def test_marketable_limit_buy_fills():
    adapter = _adapter(price=100)
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.buy,
            type=OrderType.limit,
            amount=Decimal(1),
            price=Decimal(110),  # above market -> marketable
        )
    )
    assert order.status is OrderStatus.filled


@pytest.mark.asyncio
async def test_post_only_never_takes():
    adapter = _adapter(price=100)
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.buy,
            type=OrderType.limit,
            amount=Decimal(1),
            price=Decimal(110),
            post_only=True,
        )
    )
    assert order.status is OrderStatus.open


@pytest.mark.asyncio
async def test_cancel_resting_order():
    adapter = _adapter(price=100)
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.buy,
            type=OrderType.limit,
            amount=Decimal(1),
            price=Decimal(90),
        )
    )
    canceled = await adapter.cancel_order(order.id, "BTC/USDT")
    assert canceled.status is OrderStatus.canceled
    assert await adapter.fetch_open_orders() == []


@pytest.mark.asyncio
async def test_cancel_unknown_order_raises():
    adapter = _adapter()
    with pytest.raises(NotFoundError):
        await adapter.cancel_order("missing", "BTC/USDT")


@pytest.mark.asyncio
async def test_adapter_is_paper_flag():
    adapter = _adapter()
    assert adapter.is_paper is True
    assert adapter.exchange_id.startswith("paper:")


# --- Fund reservation for resting orders ------------------------------------
async def _rest_buy(adapter, *, amount=Decimal(1), price=Decimal(90)):
    return await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.buy,
            type=OrderType.limit,
            amount=amount,
            price=price,
        )
    )


@pytest.mark.asyncio
async def test_resting_buy_reserves_quote_funds():
    adapter = _adapter(price=100, balance=1000)
    await _rest_buy(adapter, amount=Decimal(2), price=Decimal(90))

    usdt = {b.asset: b for b in await adapter.fetch_balance()}["USDT"]
    # 2 x 90 = 180, plus the taker fee that the fill would charge.
    assert usdt.used > Decimal(180)
    assert usdt.free == Decimal(1000) - usdt.used
    assert usdt.total == Decimal(1000)  # nothing was spent, only held


@pytest.mark.asyncio
async def test_resting_sell_reserves_base_asset():
    adapter = _adapter(price=100, balance=10_000)
    await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT", side=OrderSide.buy, type=OrderType.market, amount=Decimal(3)
        )
    )
    await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.sell,
            type=OrderType.limit,
            amount=Decimal(2),
            price=Decimal(150),  # above market -> rests
        )
    )
    btc = {b.asset: b for b in await adapter.fetch_balance()}["BTC"]
    assert btc.used == Decimal(2)
    assert btc.free == Decimal(1)


@pytest.mark.asyncio
async def test_cancelling_a_resting_order_releases_the_reservation():
    adapter = _adapter(price=100, balance=1000)
    order = await _rest_buy(adapter, amount=Decimal(2), price=Decimal(90))

    await adapter.cancel_order(order.id, "BTC/USDT")

    usdt = {b.asset: b for b in await adapter.fetch_balance()}["USDT"]
    assert usdt.used == Decimal(0)
    assert usdt.free == Decimal(1000)


@pytest.mark.asyncio
async def test_cannot_rest_more_orders_than_the_balance_backs():
    adapter = _adapter(price=100, balance=200)
    await _rest_buy(adapter, amount=Decimal(2), price=Decimal(90))  # holds ~180
    with pytest.raises(InsufficientBalanceError):
        await _rest_buy(adapter, amount=Decimal(2), price=Decimal(90))


@pytest.mark.asyncio
async def test_reserved_funds_cannot_be_spent_by_another_order():
    adapter = _adapter(price=100, balance=200)
    await _rest_buy(adapter, amount=Decimal(2), price=Decimal(90))  # holds ~180
    with pytest.raises(InsufficientBalanceError):
        await adapter.create_order(
            OrderRequest(
                symbol="BTC/USDT",
                side=OrderSide.buy,
                type=OrderType.market,
                amount=Decimal(1),  # needs 100, only ~20 is free
            )
        )


@pytest.mark.asyncio
async def test_cancelling_twice_does_not_release_twice():
    adapter = _adapter(price=100, balance=1000)
    order = await _rest_buy(adapter)
    await adapter.cancel_order(order.id, "BTC/USDT")
    await adapter.cancel_order(order.id, "BTC/USDT")  # idempotent
    usdt = {b.asset: b for b in await adapter.fetch_balance()}["USDT"]
    assert usdt.free == Decimal(1000)
    assert usdt.used == Decimal(0)
