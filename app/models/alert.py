"""Alert rule model."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class AlertRule(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "alert_rules"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # e.g. "price", "rsi", "funding_rate", "pnl", "balance"
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # comparison operator: "gt" | "lt" | "gte" | "lte"
    operator: Mapped[str] = mapped_column(String(4), nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(36, 18), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
