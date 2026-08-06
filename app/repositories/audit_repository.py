from __future__ import annotations

from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    model = AuditLog

    async def record(
        self,
        *,
        action: str,
        user_id: str | None = None,
        ip_address: str | None = None,
        detail: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            action=action,
            user_id=user_id,
            ip_address=ip_address,
            detail=detail or {},
        )
        return await self.add(entry)
