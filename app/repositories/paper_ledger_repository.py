"""Persistence for the durable paper-trading ledger.

Loads and stores per-account simulated balances, and reconstructs resting paper
orders from the persisted :class:`OrderRecord`s so the in-memory ledger the
adapter works against is a faithful, restart-surviving view.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exchange.models import Balance, Order
from app.exchange.paper_store import PaperLedger
from app.models.order import OrderRecord
from app.models.paper_balance import PaperBalanceRecord
from app.trading.enums import OrderSide, OrderStatus, OrderType

_OPEN_STATUSES = (OrderStatus.open.value, OrderStatus.partially_filled.value)


class PaperLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(
        self,
        account_id: str,
        *,
        quote_asset: str = "USDT",
        starting_balance: Decimal = Decimal(100_000),
        for_update: bool = False,
    ) -> PaperLedger:
        """Build a :class:`PaperLedger` for an account from persisted state.

        Seeds the starting quote balance the first time an account is used.
        ``for_update`` takes a row lock (Postgres) to serialise concurrent
        mutations of the same account; it is a no-op on SQLite.
        """
        stmt = select(PaperBalanceRecord).where(PaperBalanceRecord.account_id == account_id)
        if for_update:
            stmt = stmt.with_for_update()
        rows = (await self.session.execute(stmt)).scalars().all()

        balances: dict[str, Balance] = {
            r.asset: Balance(r.asset, Decimal(r.free), Decimal(r.used)) for r in rows
        }
        if not balances:
            balances[quote_asset] = Balance(quote_asset, Decimal(str(starting_balance)), Decimal(0))

        ledger = PaperLedger(quote_asset=quote_asset, starting_balance=starting_balance)
        ledger.balances = balances
        ledger.orders = await self._load_open_orders(account_id)
        return ledger

    async def _load_open_orders(self, account_id: str) -> dict[str, Order]:
        stmt = select(OrderRecord).where(
            OrderRecord.account_id == account_id,
            OrderRecord.is_paper.is_(True),
            OrderRecord.status.in_(_OPEN_STATUSES),
        )
        records = (await self.session.execute(stmt)).scalars().all()
        orders: dict[str, Order] = {}
        for rec in records:
            if not rec.exchange_order_id:
                continue
            orders[rec.exchange_order_id] = Order(
                id=rec.exchange_order_id,
                symbol=rec.symbol,
                side=OrderSide(rec.side),
                type=OrderType(rec.type),
                status=OrderStatus(rec.status),
                amount=Decimal(rec.amount),
                filled=Decimal(rec.filled),
                price=Decimal(rec.price) if rec.price is not None else None,
                average=Decimal(rec.average) if rec.average is not None else None,
                timestamp=None,
                client_order_id=rec.client_order_id,
                fee=Decimal(rec.fee),
                is_paper=True,
            )
        return orders

    async def save(self, account_id: str, ledger: PaperLedger) -> None:
        """Upsert the ledger's balances (orders are persisted via OrderRecord)."""
        existing = {
            r.asset: r
            for r in (
                await self.session.execute(
                    select(PaperBalanceRecord).where(PaperBalanceRecord.account_id == account_id)
                )
            )
            .scalars()
            .all()
        }
        for asset, bal in ledger.balances.items():
            row = existing.get(asset)
            if row is None:
                self.session.add(
                    PaperBalanceRecord(
                        account_id=account_id, asset=asset, free=bal.free, used=bal.used
                    )
                )
            else:
                row.free = bal.free
                row.used = bal.used
        await self.session.flush()
