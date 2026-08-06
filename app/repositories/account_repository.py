from __future__ import annotations

from sqlalchemy import select

from app.models.account import ExchangeAccount
from app.repositories.base import BaseRepository


class AccountRepository(BaseRepository[ExchangeAccount]):
    model = ExchangeAccount

    async def list_for_user(self, user_id: str) -> list[ExchangeAccount]:
        result = await self.session.execute(
            select(ExchangeAccount)
            .where(ExchangeAccount.user_id == user_id)
            .order_by(ExchangeAccount.created_at)
        )
        return list(result.scalars().all())

    async def get_for_user(self, account_id: str, user_id: str) -> ExchangeAccount | None:
        result = await self.session.execute(
            select(ExchangeAccount).where(
                ExchangeAccount.id == account_id,
                ExchangeAccount.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()
