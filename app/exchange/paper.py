"""In-memory paper-trading adapter.

Simulates order placement and fills against a real (or stubbed) market-data
source, without ever touching a live account. This is what powers the
safe-by-default trading mode: when live trading is disabled, or an account is
explicitly a paper account, every order flows through here.

Fill model (intentionally simple, deterministic, and honest about its limits):

* **Market** orders fill immediately at the current ticker ``last`` price.
* **Limit** orders fill immediately if they are marketable (buy ≥ ask/last, or
  sell ≤ bid/last); otherwise they rest as ``open`` and can be cancelled.
* A resting order **reserves** the funds it would need (quote + fee for a buy,
  base for a sell) by moving them from ``free`` to ``used``, and cancelling
  releases them. Without that, an account could rest orders worth far more than
  it holds — which would make the simulation flatter the strategy being tested.
* A flat percentage taker fee is applied. Slippage is not modelled.

The ledger is the in-memory working copy for one operation; durability lives in
the service/repository layer.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.analytics.models import Candle
from app.core.exceptions import InsufficientBalanceError, NotFoundError
from app.core.logging import get_logger
from app.exchange.base import ExchangeAdapter, MarketDataSource
from app.exchange.models import (
    Balance,
    ExchangeStatus,
    Order,
    OrderBook,
    OrderRequest,
    Position,
    Ticker,
)
from app.exchange.paper_store import PaperLedger
from app.trading.enums import OrderSide, OrderStatus, OrderType

log = get_logger(__name__)

_DEFAULT_FEE = Decimal("0.0004")  # 4 bps taker fee


class PaperAdapter(ExchangeAdapter):
    """Simulated trading over a delegated market-data source.

    Simulated balances and orders live in an injected :class:`PaperLedger` so
    they persist across the per-request adapter instances (see
    :mod:`app.exchange.paper_store`). When no ledger is supplied (e.g. unit
    tests) a standalone one is created for the adapter's lifetime.
    """

    def __init__(
        self,
        market: MarketDataSource,
        *,
        quote_asset: str = "USDT",
        starting_balance: Decimal | float = Decimal(100_000),
        fee_rate: Decimal = _DEFAULT_FEE,
        ledger: PaperLedger | None = None,
    ) -> None:
        self.exchange_id = f"paper:{market.exchange_id}"
        self._market = market
        self._quote = quote_asset
        self._fee_rate = Decimal(str(fee_rate))
        self._ledger = ledger or PaperLedger(
            quote_asset=quote_asset, starting_balance=Decimal(str(starting_balance))
        )

    @property
    def _balances(self) -> dict[str, Balance]:
        return self._ledger.balances

    @property
    def _orders(self) -> dict[str, Order]:
        return self._ledger.orders

    @staticmethod
    def _new_order_id() -> str:
        # Process-global unique id so persisted paper orders never collide.
        return f"paper-{uuid.uuid4().hex[:16]}"

    @property
    def is_paper(self) -> bool:
        return True

    async def close(self) -> None:
        # Delegate to the wrapped market-data source so its underlying network
        # client (e.g. the ccxt aiohttp session) is released — otherwise the
        # default paper path leaks a socket on every request.
        close = getattr(self._market, "close", None)
        if close is not None:
            await close()

    async def status(self) -> ExchangeStatus:
        return ExchangeStatus(self.exchange_id, ok=True, latency_ms=0.0, message="paper")

    # -- market data delegates ---------------------------------------------
    async def fetch_ticker(self, symbol: str) -> Ticker:
        return await self._market.fetch_ticker(symbol)

    async def fetch_order_book(self, symbol: str, limit: int = 50) -> OrderBook:
        return await self._market.fetch_order_book(symbol, limit)

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[Candle]:
        return await self._market.fetch_ohlcv(symbol, timeframe, limit)

    # -- account ------------------------------------------------------------
    async def fetch_balance(self) -> list[Balance]:
        return [b for b in self._balances.values() if b.total > 0]

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[Position]:
        # Spot-style paper simulation keeps balances, not derivative positions.
        return []

    def _credit(self, asset: str, amount: Decimal) -> None:
        bal = self._balances.get(asset, Balance(asset, Decimal(0), Decimal(0)))
        self._balances[asset] = Balance(asset, bal.free + amount, bal.used)

    def _debit(self, asset: str, amount: Decimal) -> None:
        bal = self._balances.get(asset, Balance(asset, Decimal(0), Decimal(0)))
        if bal.free < amount:
            raise InsufficientBalanceError(
                f"Insufficient {asset}: need {amount}, have {bal.free}.",
                details={"asset": asset},
            )
        self._balances[asset] = Balance(asset, bal.free - amount, bal.used)

    def _reserve(self, asset: str, amount: Decimal) -> None:
        """Move ``amount`` from free to used, backing a resting order."""
        bal = self._balances.get(asset, Balance(asset, Decimal(0), Decimal(0)))
        if bal.free < amount:
            raise InsufficientBalanceError(
                f"Insufficient {asset}: need {amount}, have {bal.free}.",
                details={"asset": asset},
            )
        self._balances[asset] = Balance(asset, bal.free - amount, bal.used + amount)

    def _release(self, asset: str, amount: Decimal) -> None:
        """Return reserved funds to free (never below zero used)."""
        bal = self._balances.get(asset, Balance(asset, Decimal(0), Decimal(0)))
        released = min(amount, bal.used)
        self._balances[asset] = Balance(asset, bal.free + released, bal.used - released)

    def _reservation(self, order: Order) -> tuple[str, Decimal]:
        """The (asset, amount) a resting ``order`` holds reserved."""
        base, quote = _split_symbol(order.symbol, self._quote)
        if order.side is OrderSide.buy:
            price = order.price or Decimal(0)
            cost = order.amount * price
            return quote, cost + cost * self._fee_rate
        return base, order.amount

    # -- trading ------------------------------------------------------------
    async def create_order(self, request: OrderRequest) -> Order:
        ticker = await self._market.fetch_ticker(request.symbol)
        base, quote = _split_symbol(request.symbol, self._quote)

        fill_price = self._resolve_fill_price(request, ticker)
        order_id = self._new_order_id()

        if fill_price is None:
            # Resting order — hold the funds it would consume so the account
            # cannot rest more orders than it can actually pay for.
            order = Order(
                id=order_id,
                symbol=request.symbol,
                side=request.side,
                type=request.type,
                status=OrderStatus.open,
                amount=request.amount,
                filled=Decimal(0),
                price=request.price,
                average=None,
                timestamp=ticker.timestamp,
                client_order_id=request.client_order_id,
                is_paper=True,
            )
            asset, reserved = self._reservation(order)
            self._reserve(asset, reserved)
            self._orders[order_id] = order
            log.info(
                "paper_order_resting",
                order_id=order_id,
                symbol=request.symbol,
                reserved_asset=asset,
                reserved=str(reserved),
            )
            return order

        cost = request.amount * fill_price
        fee = cost * self._fee_rate
        if request.side is OrderSide.buy:
            self._debit(quote, cost + fee)
            self._credit(base, request.amount)
        else:
            self._debit(base, request.amount)
            self._credit(quote, cost - fee)

        order = Order(
            id=order_id,
            symbol=request.symbol,
            side=request.side,
            type=request.type,
            status=OrderStatus.filled,
            amount=request.amount,
            filled=request.amount,
            price=request.price,
            average=fill_price,
            timestamp=ticker.timestamp,
            client_order_id=request.client_order_id,
            fee=fee,
            is_paper=True,
        )
        self._orders[order_id] = order
        log.info(
            "paper_order_filled",
            order_id=order_id,
            symbol=request.symbol,
            side=request.side.value,
            price=str(fill_price),
        )
        return order

    def _resolve_fill_price(self, request: OrderRequest, ticker: Ticker) -> Decimal | None:
        """Return the fill price, or ``None`` if a limit order should rest.

        A marketable limit order executes as a *taker* and never fills worse than
        the market: a buy fills at ``min(limit, last)`` and a sell at
        ``max(limit, last)``. Returning the limit price verbatim (as an earlier
        version did) would mis-price aggressive limits.
        """
        if request.type is OrderType.market:
            return ticker.last
        if request.type is OrderType.limit and request.price is not None:
            if request.post_only:
                return None  # post-only never takes liquidity in this model
            if request.side is OrderSide.buy and request.price >= ticker.last:
                return min(request.price, ticker.last)
            if request.side is OrderSide.sell and request.price <= ticker.last:
                return max(request.price, ticker.last)
            return None  # non-marketable → rests
        # Stop/conditional orders rest until triggered (not simulated intraday).
        return None

    async def cancel_order(self, order_id: str, symbol: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise NotFoundError(f"Paper order {order_id} not found.")
        if order.status in {OrderStatus.filled, OrderStatus.canceled}:
            return order
        # Give the reserved funds back before marking the order cancelled.
        asset, reserved = self._reservation(order)
        self._release(asset, reserved)
        canceled = Order(
            id=order.id,
            symbol=order.symbol,
            side=order.side,
            type=order.type,
            status=OrderStatus.canceled,
            amount=order.amount,
            filled=order.filled,
            price=order.price,
            average=order.average,
            timestamp=order.timestamp,
            client_order_id=order.client_order_id,
            fee=order.fee,
            is_paper=True,
        )
        self._orders[order_id] = canceled
        return canceled

    async def fetch_order(self, order_id: str, symbol: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise NotFoundError(f"Paper order {order_id} not found.")
        return order

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        return [
            o
            for o in self._orders.values()
            if o.status in {OrderStatus.open, OrderStatus.partially_filled}
            and (symbol is None or o.symbol == symbol)
        ]


def _split_symbol(symbol: str, default_quote: str) -> tuple[str, str]:
    """Split a ``BASE/QUOTE`` (optionally ``:SETTLE``) symbol into (base, quote)."""
    core = symbol.split(":", 1)[0]
    if "/" in core:
        base, quote = core.split("/", 1)
        return base, quote
    return core, default_quote


def new_client_order_id(prefix: str = "cth") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"
