"""Durable paper-trading balances.

Persisting simulated balances makes paper accounts survive process restarts and
be shared across replicas (backed by the same database). Paper *orders* are
already durable in :class:`~app.models.order.OrderRecord`; this table holds the
per-asset cash/holdings ledger.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class PaperBalanceRecord(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "paper_balances"
    __table_args__ = (UniqueConstraint("account_id", "asset", name="uq_paper_balance"),)

    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("exchange_accounts.id", ondelete="CASCADE"), index=True
    )
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    free: Mapped[Decimal] = mapped_column(Numeric(36, 18), default=0, nullable=False)
    used: Mapped[Decimal] = mapped_column(Numeric(36, 18), default=0, nullable=False)
