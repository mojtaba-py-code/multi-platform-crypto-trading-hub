"""Tests for the live-exchange adapter's normalisation and error mapping.

No network and no ccxt install is needed: the adapter's lazily-built client is
replaced with a stub. What is under test is the translation layer — ccxt's
loosely-typed dicts into the application's DTOs, and ccxt's exception names into
the application's exception hierarchy. Mistakes there are the kind that only
show up against a real exchange, so they are pinned here instead.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.exceptions import ExchangeError, ExchangeUnavailableError, UnsupportedExchangeError
from app.core.resilience import reset_circuit_breakers
from app.exchange.ccxt_adapter import CcxtAdapter
from app.exchange.credentials import ExchangeCredentials
from app.exchange.models import OrderRequest
from app.trading.enums import MarginMode, OrderSide, OrderStatus, OrderType, PositionSide


class FakeCcxtClient:
    """Minimal stand-in for ``ccxt.async_support.Exchange``."""

    def __init__(self, **responses) -> None:
        self._responses = responses
        self.calls: list[tuple] = []
        self.closed = False

    def _respond(self, method: str, args: tuple):
        self.calls.append((method, args))
        value = self._responses.get(method)
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self) -> None:
        self.closed = True

    def __getattr__(self, method: str):
        async def _call(*args, **kwargs):
            return self._respond(method, args)

        return _call


def _adapter(exchange_id: str = "binance", **responses) -> CcxtAdapter:
    # Breakers are shared per exchange id, so clear them first: failures injected
    # by one test must not leave a tripped circuit for the next.
    reset_circuit_breakers()
    adapter = CcxtAdapter(exchange_id)
    adapter._client = FakeCcxtClient(**responses)
    return adapter


# --- Construction -----------------------------------------------------------
def test_unknown_exchange_is_rejected_at_construction():
    with pytest.raises(UnsupportedExchangeError):
        CcxtAdapter("not-a-real-exchange")


def test_adapter_is_never_paper():
    assert _adapter().is_paper is False


@pytest.mark.asyncio
async def test_close_releases_the_underlying_client():
    adapter = _adapter()
    client = adapter._client
    await adapter.close()
    assert client.closed is True
    # Idempotent: closing again must not explode.
    await adapter.close()


# --- Market data ------------------------------------------------------------
@pytest.mark.asyncio
async def test_ticker_is_normalised_to_decimals():
    adapter = _adapter(
        fetch_ticker={
            "last": "68000.5",
            "bid": 67999.0,
            "ask": 68001.0,
            "high": None,
            "low": "",
            "baseVolume": "12.5",
            "timestamp": 1_700_000_000_000,
        }
    )
    ticker = await adapter.fetch_ticker("BTC/USDT")

    assert ticker.last == Decimal("68000.5")
    assert ticker.bid == Decimal("67999.0")
    # Missing and empty-string fields become None rather than Decimal("0").
    assert ticker.high is None
    assert ticker.low is None
    assert ticker.base_volume == Decimal("12.5")


@pytest.mark.asyncio
async def test_order_book_levels_and_spread():
    adapter = _adapter(
        fetch_order_book={
            "bids": [["99.5", "1.0"], ["99.0", "2.0"]],
            "asks": [["100.5", "1.5"]],
            "timestamp": 1,
        }
    )
    book = await adapter.fetch_order_book("BTC/USDT", limit=2)

    assert book.bids[0].price == Decimal("99.5")
    assert book.asks[0].amount == Decimal("1.5")
    assert book.spread == Decimal("1.0")


@pytest.mark.asyncio
async def test_ohlcv_rows_become_candles():
    adapter = _adapter(fetch_ohlcv=[[1_700_000_000_000, 1, 2, 0.5, 1.5, 10]])
    candles = await adapter.fetch_ohlcv("BTC/USDT", "1h", 1)

    assert len(candles) == 1
    assert candles[0].high == Decimal("2")
    assert candles[0].close == Decimal("1.5")


# --- Balances and positions -------------------------------------------------
@pytest.mark.asyncio
async def test_balances_merge_free_and_used_and_drop_empty_assets():
    adapter = _adapter(
        fetch_balance={
            "free": {"USDT": "1000", "BTC": "0", "ETH": "0"},
            "used": {"USDT": "250", "ETH": "0"},
        }
    )
    balances = {b.asset: b for b in await adapter.fetch_balance()}

    assert set(balances) == {"USDT"}  # zero-total assets are omitted
    assert balances["USDT"].free == Decimal(1000)
    assert balances["USDT"].used == Decimal(250)
    assert balances["USDT"].total == Decimal(1250)


@pytest.mark.asyncio
async def test_positions_skip_flat_rows_and_tolerate_null_fields():
    adapter = _adapter(
        fetch_positions=[
            {"symbol": "BTC/USDT:USDT", "contracts": "0"},  # flat -> skipped
            {
                "symbol": "ETH/USDT:USDT",
                "contracts": "2",
                # ccxt can send an explicit null here; the enum must not choke.
                "side": None,
                "marginMode": None,
                "entryPrice": "3000",
                "markPrice": "3100",
                "leverage": None,
                "unrealizedPnl": "200",
                "liquidationPrice": None,
            },
        ]
    )
    positions = await adapter.fetch_positions()

    assert len(positions) == 1
    position = positions[0]
    assert position.symbol == "ETH/USDT:USDT"
    assert position.side is PositionSide.long  # defaulted
    assert position.margin_mode is MarginMode.cross  # defaulted
    assert position.leverage == 1
    assert position.liquidation_price is None


# --- Orders -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_order_forwards_flags_and_parses_the_response():
    adapter = _adapter(
        create_order={
            "id": "77",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "status": "closed",
            "amount": "1",
            "filled": "1",
            "price": "68000",
            "average": "67995",
            "clientOrderId": "cth-abc",
            "fee": {"cost": "0.27"},
        }
    )
    order = await adapter.create_order(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.buy,
            type=OrderType.limit,
            amount=Decimal(1),
            price=Decimal(68_000),
            reduce_only=True,
            post_only=True,
            stop_price=Decimal(67_000),
            client_order_id="cth-abc",
        )
    )

    # ccxt reports a fully-filled order as "closed"; we call that filled.
    assert order.status is OrderStatus.filled
    assert order.fee == Decimal("0.27")
    assert order.is_paper is False

    _, args = adapter._client.calls[-1]
    params = args[-1]
    assert params["reduceOnly"] is True
    assert params["postOnly"] is True
    assert params["stopPrice"] == 67_000.0
    assert params["clientOrderId"] == "cth-abc"


@pytest.mark.asyncio
async def test_unknown_order_type_and_status_fall_back_safely():
    adapter = _adapter(
        fetch_order={"id": "1", "type": "something_exotic", "status": "weird", "amount": "1"}
    )
    order = await adapter.fetch_order("1", "BTC/USDT")

    assert order.type is OrderType.limit
    assert order.status is OrderStatus.open
    assert order.fee == Decimal(0)  # absent fee is zero, not None


@pytest.mark.asyncio
async def test_cancel_and_list_open_orders():
    adapter = _adapter(
        cancel_order={"id": "1", "status": "cancelled", "amount": "1"},
        fetch_open_orders=[{"id": "2", "symbol": "ETH/USDT", "status": "open", "amount": "3"}],
    )
    # ccxt spells it both ways depending on the exchange.
    assert (await adapter.cancel_order("1", "BTC/USDT")).status is OrderStatus.canceled

    open_orders = await adapter.fetch_open_orders()
    assert [o.symbol for o in open_orders] == ["ETH/USDT"]


# --- Error mapping ----------------------------------------------------------
class NetworkError(Exception):
    """Mimics ``ccxt.NetworkError`` by class name, which is what the adapter matches."""


class InsufficientFunds(Exception):
    pass


@pytest.mark.asyncio
async def test_transient_ccxt_errors_become_exchange_unavailable():
    adapter = _adapter(fetch_ticker=NetworkError("connection reset"))
    with pytest.raises(ExchangeUnavailableError):
        await adapter.fetch_ticker("BTC/USDT")


@pytest.mark.asyncio
async def test_other_ccxt_errors_become_exchange_errors():
    adapter = _adapter(create_order=InsufficientFunds("balance too low"))
    with pytest.raises(ExchangeError) as excinfo:
        await adapter.create_order(
            OrderRequest(
                symbol="BTC/USDT", side=OrderSide.sell, type=OrderType.market, amount=Decimal(1)
            )
        )
    assert excinfo.value.details["exchange"] == "binance"


# --- Health -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_status_reports_ok_with_latency():
    adapter = _adapter(fetch_time=1_700_000_000_000)
    status = await adapter.status()
    assert status.ok is True
    assert status.latency_ms is not None


@pytest.mark.asyncio
async def test_status_reports_failure_without_raising():
    adapter = _adapter(fetch_time=NetworkError("down"))
    status = await adapter.status()
    assert status.ok is False
    assert status.message


# --- Credentials ------------------------------------------------------------
def test_credentials_never_appear_in_the_repr():
    creds = ExchangeCredentials(api_key="AK-live", api_secret="SK-live", passphrase="pp")
    rendered = repr(creds)
    for secret in ("AK-live", "SK-live", "pp"):
        assert secret not in rendered
    assert creds.is_complete()
