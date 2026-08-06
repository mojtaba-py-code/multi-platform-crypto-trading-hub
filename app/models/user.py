"""User account model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey
from app.security.rbac import Role

if TYPE_CHECKING:
    from app.models.account import ExchangeAccount


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    # Argon2id hash — never the plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default=Role.trader.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Two-factor authentication. The TOTP secret is stored encrypted.
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_secret_enc: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Highest TOTP time step already accepted for this user. Codes at or below
    # it are refused, making every code single-use (replay protection).
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    accounts: Mapped[list[ExchangeAccount]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"User(id={self.id!r}, email={self.email!r}, role={self.role!r})"
