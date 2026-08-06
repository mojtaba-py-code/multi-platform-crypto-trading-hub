"""Exchange account model — stores encrypted API credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.user import User


class ExchangeAccount(UUIDPrimaryKey, TimestampMixin, Base):
    """A user's connection to one exchange.

    API secrets are stored **only** as ciphertext produced by
    :class:`~app.security.crypto.SecretCipher`. The plaintext is supplied on
    creation, encrypted immediately, and never persisted or logged.
    """

    __tablename__ = "exchange_accounts"
    __table_args__ = (UniqueConstraint("user_id", "exchange_id", "label", name="uq_account_label"),)

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    exchange_id: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), default="spot", nullable=False)

    # Paper accounts never carry real credentials.
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    api_key_enc: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    api_secret_enc: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    passphrase_enc: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship(back_populates="accounts")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ExchangeAccount(id={self.id!r}, exchange={self.exchange_id!r}, "
            f"label={self.label!r}, paper={self.is_paper})"
        )
