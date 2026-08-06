"""Order placement and management, with pre-trade risk checks."""

from __future__ import annotations

from decimal import Decimal

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.exchange.models import Order, OrderRequest, Position
from app.exchange.paper import new_client_order_id
from app.models.account import ExchangeAccount
from app.models.order import OrderRecord
from app.repositories.audit_repository import AuditRepository
from app.repositories.order_repository import OrderRepository
from app.services.account_service import AccountService
from app.trading.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from app.trading.risk import RiskLimits

log = get_logger(__name__)


class TradingService:
    def __init__(
        self,
        *,
        accounts: AccountService,
        orders: OrderRepository,
        audit: AuditRepository,
        risk_limits: RiskLimits | None = None,
    ) -> None:
        self._accounts = accounts
        self._orders = orders
        self._audit = audit
        self._risk = risk_limits or RiskLimits()

    async def place_order(
        self,
        *,
        user_id: str,
        account_id: str,
        symbol: str,
        side: OrderSide,
        type_: OrderType,
        amount: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        time_in_force: TimeInForce = TimeInForce.gtc,
        reduce_only: bool = False,
        post_only: bool = False,
        leverage: int | None = None,
    ) -> OrderRecord:
        account = await self._accounts.get_account(account_id=account_id, user_id=user_id)
        async with self._accounts.adapter_session(account, persist=True) as adapter:
            request = OrderRequest(
                symbol=symbol,
                side=side,
                type=type_,
                amount=amount,
                price=price,
                stop_price=stop_price,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                post_only=post_only,
                leverage=leverage,
                client_order_id=new_client_order_id(),
            )
            # Pre-trade risk check. Notional uses the limit price when available,
            # else the current mark price. Equity is approximated by the account's
            # free+used quote-asset balance; when it cannot be determined the
            # position-size check is skipped but the leverage ceiling still applies.
            ref_price = price
            if ref_price is None:
                ticker = await adapter.fetch_ticker(symbol)
                ref_price = ticker.last
            equity = await self._quote_equity(adapter, symbol)
            self._risk.check_order(
                notional=amount * ref_price,
                equity=equity if equity > 0 else amount * ref_price * self._risk.max_leverage,
                leverage=Decimal(leverage or 1),
            )

            order = await adapter.create_order(request)
            record = await self._persist(user_id, account, order)
            await self._audit.record(
                action="order.place",
                user_id=user_id,
                detail={
                    "account": account_id,
                    "symbol": symbol,
                    "side": side.value,
                    "type": type_.value,
                    "paper": order.is_paper,
                    "status": order.status.value,
                },
            )
            log.info(
                "order_placed",
                user_id=user_id,
                symbol=symbol,
                paper=order.is_paper,
                status=order.status.value,
            )
            return record

    async def get_order(self, *, user_id: str, order_id: str) -> OrderRecord:
        record = await self._orders.get_for_user(order_id, user_id)
        if record is None:
            raise NotFoundError("Order not found.")
        return record

    async def cancel_order(self, *, user_id: str, order_id: str) -> OrderRecord:
        """Cancel an order by its local record id and persist the state change.

        Operates on the persisted :class:`OrderRecord` (the source of truth for
        the API), rather than a freshly-built adapter's ephemeral ledger. The
        exchange/paper adapter is asked to cancel too, so the paper ledger and a
        live exchange stay consistent; the DB record is then updated in place.
        """
        record = await self.get_order(user_id=user_id, order_id=order_id)
        if record.status in {OrderStatus.canceled.value, OrderStatus.filled.value}:
            return record  # idempotent — nothing to cancel

        account = await self._accounts.get_account(account_id=record.account_id, user_id=user_id)
        # ``persist=True``: cancelling a resting paper order releases the funds
        # it had reserved, and that balance change has to be written back.
        async with self._accounts.adapter_session(account, persist=True) as adapter:
            if record.exchange_order_id:
                try:
                    result = await adapter.cancel_order(record.exchange_order_id, record.symbol)
                    record.status = result.status.value
                except NotFoundError:
                    # The adapter no longer knows this order (e.g. process restart
                    # cleared the paper ledger); the local record is authoritative.
                    record.status = OrderStatus.canceled.value
            else:
                record.status = OrderStatus.canceled.value
            await self._orders.session.flush()
            await self._audit.record(
                action="order.cancel",
                user_id=user_id,
                detail={"account": record.account_id, "order": order_id},
            )
            return record

    async def list_open_orders(self, *, user_id: str, account_id: str) -> list[Order]:
        account = await self._accounts.get_account(account_id=account_id, user_id=user_id)
        async with self._accounts.adapter_session(account) as adapter:
            return await adapter.fetch_open_orders()

    async def list_positions(self, *, user_id: str, account_id: str) -> list[Position]:
        account = await self._accounts.get_account(account_id=account_id, user_id=user_id)
        async with self._accounts.adapter_session(account) as adapter:
            return await adapter.fetch_positions()

    @staticmethod
    async def _quote_equity(adapter, symbol: str) -> Decimal:
        """Best-effort account equity in the pair's quote asset.

        Returns 0 if balances cannot be read (e.g. a live account with a
        transient error), signalling the caller to skip the size check.
        """
        core = symbol.split(":", 1)[0]
        quote = core.split("/", 1)[1] if "/" in core else "USDT"
        try:
            balances = await adapter.fetch_balance()
        except Exception:  # noqa: BLE001 - risk check must not block on read errors
            return Decimal(0)
        for bal in balances:
            if bal.asset == quote:
                return bal.total
        return Decimal(0)

    async def _persist(self, user_id: str, account: ExchangeAccount, order: Order) -> OrderRecord:
        record = OrderRecord(
            user_id=user_id,
            account_id=account.id,
            exchange_order_id=order.id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side.value,
            type=order.type.value,
            status=order.status.value,
            amount=order.amount,
            filled=order.filled,
            price=order.price,
            average=order.average,
            fee=order.fee,
            is_paper=order.is_paper,
        )
        return await self._orders.add(record)

    async def list_orders(
        self,
        *,
        user_id: str,
        symbol: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[OrderRecord], int]:
        offset = (page - 1) * page_size
        items = await self._orders.list_for_user(
            user_id, symbol=symbol, status=status, limit=page_size, offset=offset
        )
        total = await self._orders.count_for_user(user_id, symbol=symbol, status=status)
        return items, total
