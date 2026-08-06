"""Persisted order record (local mirror of exchange/paper orders)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class OrderRecord(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "orders"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("exchange_accounts.id", ondelete="CASCADE"), index=True
    )
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(36, 18), nullable=False)
    filled: Mapped[Decimal] = mapped_column(Numeric(36, 18), default=0, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(36, 18), nullable=True)
    average: Mapped[Decimal | None] = mapped_column(Numeric(36, 18), nullable=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(36, 18), default=0, nullable=False)

    is_paper: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
