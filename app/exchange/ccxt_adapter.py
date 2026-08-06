"""Concrete exchange adapter backed by ccxt's async client.

Wraps ``ccxt.async_support`` and normalises every response into the DTOs in
:mod:`app.exchange.models`. All ccxt exceptions are translated into the
application's exception hierarchy, and a per-instance circuit breaker guards
against hammering an unhealthy exchange.

``ccxt`` is imported lazily so the rest of the package (and the whole test
suite for pure logic) can run without the dependency installed.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.analytics.models import Candle
from app.core.exceptions import (
    ExchangeError,
    ExchangeUnavailableError,
    UnsupportedExchangeError,
)
from app.core.logging import get_logger
from app.core.resilience import get_circuit_breaker
from app.exchange.base import ExchangeAdapter
from app.exchange.credentials import ExchangeCredentials
from app.exchange.models import (
    Balance,
    ExchangeStatus,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    Position,
    Ticker,
)
from app.exchange.registry import get_info
from app.trading.enums import (
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)

if TYPE_CHECKING:  # pragma: no cover
    import ccxt.async_support as ccxt_async

log = get_logger(__name__)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _dec0(value: Any) -> Decimal:
    return _dec(value) or Decimal(0)


_STATUS_MAP = {
    "open": OrderStatus.open,
    "closed": OrderStatus.filled,
    "canceled": OrderStatus.canceled,
    "cancelled": OrderStatus.canceled,
    "expired": OrderStatus.expired,
    "rejected": OrderStatus.rejected,
}


class CcxtAdapter(ExchangeAdapter):
    """Adapter over a single ccxt exchange instance."""

    def __init__(
        self,
        exchange_id: str,
        credentials: ExchangeCredentials | None = None,
        *,
        default_market_type: str = "spot",
    ) -> None:
        info = get_info(exchange_id)
        if info is None:
            raise UnsupportedExchangeError(details={"exchange": exchange_id})
        self.exchange_id = info.id
        self._info = info
        self._credentials = credentials
        self._default_market_type = default_market_type
        # Shared per-exchange breaker so state survives the per-request adapter
        # lifecycle (adapters are built and closed on every operation).
        self._breaker = get_circuit_breaker(f"ccxt:{info.id}")
        self._client: ccxt_async.Exchange | None = None

    # -- lifecycle ----------------------------------------------------------
    def _build_client(self) -> ccxt_async.Exchange:
        try:
            import ccxt.async_support as ccxt_async
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ExchangeUnavailableError(
                "ccxt is not installed; install it to use live exchange connectivity."
            ) from exc

        config: dict[str, Any] = {
            "enableRateLimit": True,
            "options": {"defaultType": self._default_market_type},
        }
        if self._credentials and self._credentials.is_complete():
            config["apiKey"] = self._credentials.api_key
            config["secret"] = self._credentials.api_secret
            if self._credentials.passphrase:
                config["password"] = self._credentials.passphrase

        exchange_cls = getattr(ccxt_async, self.exchange_id)
        client = exchange_cls(config)
        if self._credentials and self._credentials.testnet and self._info.has_testnet:
            try:
                client.set_sandbox_mode(True)
            except Exception:  # noqa: BLE001 - not all exchanges support sandbox
                log.warning("sandbox_unavailable", exchange=self.exchange_id)
        return client

    @property
    def client(self) -> ccxt_async.Exchange:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    @property
    def is_paper(self) -> bool:
        return False

    # -- internal call wrapper ---------------------------------------------
    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        async def _invoke() -> Any:
            fn = getattr(self.client, method)
            return await fn(*args, **kwargs)

        try:
            return await self._breaker.call(_invoke)
        except ExchangeError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise all ccxt errors
            name = type(exc).__name__
            if name in {"NetworkError", "RequestTimeout", "ExchangeNotAvailable", "DDoSProtection"}:
                raise ExchangeUnavailableError(
                    f"{self.exchange_id} unavailable: {exc}", details={"exchange": self.exchange_id}
                ) from exc
            raise ExchangeError(
                f"{self.exchange_id} error on {method}: {exc}",
                details={"exchange": self.exchange_id, "method": method},
            ) from exc

    # -- health -------------------------------------------------------------
    async def status(self) -> ExchangeStatus:
        start = time.monotonic()
        try:
            await self._call("fetch_time")
        except ExchangeError as exc:
            return ExchangeStatus(self.exchange_id, ok=False, message=exc.message)
        latency = (time.monotonic() - start) * 1000
        return ExchangeStatus(self.exchange_id, ok=True, latency_ms=round(latency, 2))

    # -- market data --------------------------------------------------------
    async def fetch_ticker(self, symbol: str) -> Ticker:
        t = await self._call("fetch_ticker", symbol)
        return Ticker(
            symbol=symbol,
            last=_dec0(t.get("last")),
            bid=_dec(t.get("bid")),
            ask=_dec(t.get("ask")),
            high=_dec(t.get("high")),
            low=_dec(t.get("low")),
            base_volume=_dec(t.get("baseVolume")),
            timestamp=t.get("timestamp"),
        )

    async def fetch_order_book(self, symbol: str, limit: int = 50) -> OrderBook:
        ob = await self._call("fetch_order_book", symbol, limit)
        return OrderBook(
            symbol=symbol,
            bids=[OrderBookLevel(_dec0(p), _dec0(a)) for p, a in ob.get("bids", [])],
            asks=[OrderBookLevel(_dec0(p), _dec0(a)) for p, a in ob.get("asks", [])],
            timestamp=ob.get("timestamp"),
        )

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[Candle]:
        rows = await self._call("fetch_ohlcv", symbol, timeframe, None, limit)
        return [Candle.from_ccxt(row) for row in rows]

    # -- account ------------------------------------------------------------
    async def fetch_balance(self) -> list[Balance]:
        raw = await self._call("fetch_balance")
        free = raw.get("free", {})
        used = raw.get("used", {})
        assets = set(free) | set(used)
        balances = [
            Balance(asset=a, free=_dec0(free.get(a)), used=_dec0(used.get(a))) for a in assets
        ]
        return [b for b in balances if b.total > 0]

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[Position]:
        raw = await self._call("fetch_positions", symbols)
        positions: list[Position] = []
        for p in raw:
            contracts = _dec0(p.get("contracts"))
            if contracts == 0:
                continue
            positions.append(
                Position(
                    symbol=p.get("symbol", ""),
                    # ccxt may return an explicit ``None`` (not just a missing
                    # key) for a flat position's side/marginMode, which would
                    # make the enum constructor raise — coalesce to a default.
                    side=PositionSide(p.get("side") or "long"),
                    contracts=contracts,
                    entry_price=_dec0(p.get("entryPrice")),
                    mark_price=_dec(p.get("markPrice")),
                    leverage=int(p.get("leverage") or 1),
                    margin_mode=MarginMode(p.get("marginMode") or "cross"),
                    unrealized_pnl=_dec0(p.get("unrealizedPnl")),
                    liquidation_price=_dec(p.get("liquidationPrice")),
                )
            )
        return positions

    # -- trading ------------------------------------------------------------
    async def create_order(self, request: OrderRequest) -> Order:
        params = dict(request.params)
        if request.reduce_only:
            params["reduceOnly"] = True
        if request.post_only:
            params["postOnly"] = True
        if request.stop_price is not None:
            params["stopPrice"] = float(request.stop_price)
        if request.client_order_id:
            params["clientOrderId"] = request.client_order_id
        params["timeInForce"] = request.time_in_force.value

        raw = await self._call(
            "create_order",
            request.symbol,
            request.type.value,
            request.side.value,
            float(request.amount),
            float(request.price) if request.price is not None else None,
            params,
        )
        return self._parse_order(raw, request.symbol)

    async def cancel_order(self, order_id: str, symbol: str) -> Order:
        raw = await self._call("cancel_order", order_id, symbol)
        return self._parse_order(raw, symbol)

    async def fetch_order(self, order_id: str, symbol: str) -> Order:
        raw = await self._call("fetch_order", order_id, symbol)
        return self._parse_order(raw, symbol)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        raw = await self._call("fetch_open_orders", symbol)
        return [self._parse_order(o, o.get("symbol", symbol or "")) for o in raw]

    # -- parsing ------------------------------------------------------------
    def _parse_order(self, raw: dict[str, Any], symbol: str) -> Order:
        fee = Decimal(0)
        if raw.get("fee") and raw["fee"].get("cost") is not None:
            fee = _dec0(raw["fee"]["cost"])
        return Order(
            id=str(raw.get("id", "")),
            symbol=raw.get("symbol", symbol),
            side=OrderSide(raw.get("side", "buy")),
            type=OrderType(_normalise_type(raw.get("type", "limit"))),
            status=_STATUS_MAP.get(raw.get("status", ""), OrderStatus.open),
            amount=_dec0(raw.get("amount")),
            filled=_dec0(raw.get("filled")),
            price=_dec(raw.get("price")),
            average=_dec(raw.get("average")),
            timestamp=raw.get("timestamp"),
            client_order_id=raw.get("clientOrderId"),
            fee=fee,
            is_paper=False,
        )


def _normalise_type(ccxt_type: str) -> str:
    mapping = {
        "market": "market",
        "limit": "limit",
        "stop": "stop",
        "stop_market": "stop",
        "stop_limit": "stop_limit",
        "take_profit": "take_profit",
        "trailing_stop": "trailing_stop",
    }
    return mapping.get(ccxt_type, "limit")
