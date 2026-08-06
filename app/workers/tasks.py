"""Celery task definitions.

Tasks are thin wrappers that run an async coroutine to completion. Each task
opens its own database session (Celery workers are separate processes from the
API) and delegates to the same service layer used by the HTTP handlers, so
there is no logic duplication.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import delete, select

from app.core.logging import get_logger
from app.workers.celery_app import celery

log = get_logger(__name__)

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    """Run an async coroutine from a synchronous Celery task.

    A Celery worker is a long-lived process, but ``asyncio.run`` creates and
    closes a **new** event loop on every call. The application's async engine is
    a module-level singleton whose pooled asyncpg connections are bound to the
    loop that created them — so reusing the cached engine on a second task run
    would fail with "attached to a different loop". We therefore dispose the
    engine at the end of each run so the next run rebuilds it on its own loop.
    """

    async def _wrapped() -> T:
        from app.database.session import reset_engine

        try:
            return await coro
        finally:
            await reset_engine()

    return asyncio.run(_wrapped())


@celery.task(name="app.workers.tasks.sync_all_portfolios")
def sync_all_portfolios() -> dict:
    """Refresh cached balances for every active user.

    The heavy lifting (per-account balance fetch, failure isolation) lives in
    :class:`~app.services.portfolio_service.PortfolioService`.
    """
    return _run(_sync_all_portfolios())


async def _sync_all_portfolios() -> dict:
    from app.database.session import get_sessionmaker
    from app.exchange.factory import get_exchange_factory
    from app.models.user import User
    from app.repositories.account_repository import AccountRepository
    from app.repositories.audit_repository import AuditRepository
    from app.security.crypto import get_secret_cipher
    from app.services.account_service import AccountService
    from app.services.portfolio_service import PortfolioService

    maker = get_sessionmaker()
    synced = 0
    async with maker() as session:
        users = (await session.execute(select(User.id).where(User.is_active.is_(True)))).all()
        account_service = AccountService(
            accounts=AccountRepository(session),
            audit=AuditRepository(session),
            cipher=get_secret_cipher(),
            factory=get_exchange_factory(),
        )
        portfolio = PortfolioService(account_service)
        for (user_id,) in users:
            try:
                await portfolio.aggregate_balances(user_id)
                synced += 1
            except Exception as exc:  # noqa: BLE001 - one user must not fail the job
                log.warning("portfolio_sync_failed", user_id=user_id, error=str(exc))
    log.info("portfolios_synced", count=synced)
    return {"synced": synced}


@celery.task(name="app.workers.tasks.evaluate_alerts")
def evaluate_alerts() -> dict:
    """Placeholder for alert evaluation (price/RSI/PnL thresholds).

    Kept intentionally minimal in v1.0: it loads active rules so the wiring and
    schedule are real and testable; notification delivery is a follow-up.
    """
    return _run(_evaluate_alerts())


async def _evaluate_alerts() -> dict:
    from app.database.session import get_sessionmaker
    from app.models.alert import AlertRule

    maker = get_sessionmaker()
    async with maker() as session:
        rules = (
            (await session.execute(select(AlertRule).where(AlertRule.is_active.is_(True))))
            .scalars()
            .all()
        )
    log.info("alerts_evaluated", active_rules=len(rules))
    return {"active_rules": len(rules)}


@celery.task(name="app.workers.tasks.cleanup_old_audit_logs")
def cleanup_old_audit_logs(retention_days: int = 90) -> dict:
    """Delete audit-log rows older than the retention window."""
    return _run(_cleanup_old_audit_logs(retention_days))


async def _cleanup_old_audit_logs(retention_days: int) -> dict:
    from app.database.session import get_sessionmaker
    from app.models.audit import AuditLog

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    maker = get_sessionmaker()
    async with maker() as session:
        result = await session.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
        await session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    log.info("audit_logs_cleaned", deleted=deleted)
    return {"deleted": deleted}
