from __future__ import annotations

from sqlalchemy import func, select

from app.models.order import OrderRecord
from app.repositories.base import BaseRepository


class OrderRepository(BaseRepository[OrderRecord]):
    model = OrderRecord

    async def get_for_user(self, order_id: str, user_id: str) -> OrderRecord | None:
        result = await self.session.execute(
            select(OrderRecord).where(OrderRecord.id == order_id, OrderRecord.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: str,
        *,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OrderRecord]:
        stmt = select(OrderRecord).where(OrderRecord.user_id == user_id)
        if symbol:
            stmt = stmt.where(OrderRecord.symbol == symbol)
        if status:
            stmt = stmt.where(OrderRecord.status == status)
        stmt = stmt.order_by(OrderRecord.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(
        self, user_id: str, *, symbol: str | None = None, status: str | None = None
    ) -> int:
        stmt = select(func.count(OrderRecord.id)).where(OrderRecord.user_id == user_id)
        if symbol:
            stmt = stmt.where(OrderRecord.symbol == symbol)
        if status:
            stmt = stmt.where(OrderRecord.status == status)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
