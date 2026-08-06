"""Tests for the Celery background jobs.

The tasks themselves are thin synchronous wrappers around coroutines, so the
coroutines are exercised directly here — that is where all the behaviour lives
(session handling, per-user failure isolation, the retention cutoff).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.alert import AlertRule
from app.models.audit import AuditLog
from app.models.user import User
from app.security.crypto import get_password_hasher
from app.workers import tasks
from sqlalchemy import select


async def _make_user(session, email: str = "worker@example.com", active: bool = True) -> User:
    user = User(
        email=email,
        password_hash=get_password_hasher().hash("a-password-long-enough"),
        is_active=active,
    )
    session.add(user)
    await session.commit()
    return user


# --- _run -------------------------------------------------------------------
def test_run_executes_a_coroutine_and_returns_its_value():
    async def _work() -> str:
        return "done"

    assert tasks._run(_work()) == "done"


# --- sync_all_portfolios ----------------------------------------------------
@pytest.mark.asyncio
async def test_sync_all_portfolios_counts_active_users(session):
    await _make_user(session, "active@example.com")
    await _make_user(session, "disabled@example.com", active=False)

    result = await tasks._sync_all_portfolios()

    # Only the active user is synced; the disabled one is skipped entirely.
    assert result == {"synced": 1}


@pytest.mark.asyncio
async def test_sync_all_portfolios_isolates_a_failing_user(session, monkeypatch):
    await _make_user(session, "one@example.com")
    await _make_user(session, "two@example.com")

    async def _boom(self, user_id):  # noqa: ANN001 - test double
        raise RuntimeError("exchange is down")

    monkeypatch.setattr("app.services.portfolio_service.PortfolioService.aggregate_balances", _boom)
    # One user's failure must not abort the whole job.
    assert await tasks._sync_all_portfolios() == {"synced": 0}


@pytest.mark.asyncio
async def test_sync_all_portfolios_on_an_empty_database(session):
    assert await tasks._sync_all_portfolios() == {"synced": 0}


# --- evaluate_alerts --------------------------------------------------------
@pytest.mark.asyncio
async def test_evaluate_alerts_counts_only_active_rules(session):
    user = await _make_user(session)
    session.add_all(
        [
            AlertRule(
                user_id=user.id,
                metric="price",
                symbol="BTC/USDT",
                operator="gt",
                threshold=Decimal(70_000),
                is_active=True,
            ),
            AlertRule(
                user_id=user.id,
                metric="price",
                symbol="ETH/USDT",
                operator="lt",
                threshold=Decimal(1_000),
                is_active=False,
            ),
        ]
    )
    await session.commit()

    assert await tasks._evaluate_alerts() == {"active_rules": 1}


# --- cleanup_old_audit_logs -------------------------------------------------
@pytest.mark.asyncio
async def test_cleanup_deletes_only_rows_past_the_retention_window(session):
    user = await _make_user(session)
    old = AuditLog(user_id=user.id, action="auth.login", detail={})
    recent = AuditLog(user_id=user.id, action="auth.login", detail={})
    session.add_all([old, recent])
    await session.commit()

    # Backdate one row beyond the window.
    old.created_at = datetime.now(UTC) - timedelta(days=120)
    await session.commit()

    result = await tasks._cleanup_old_audit_logs(retention_days=90)
    assert result == {"deleted": 1}

    remaining = (await session.execute(select(AuditLog.id))).scalars().all()
    assert remaining == [recent.id]


@pytest.mark.asyncio
async def test_cleanup_is_a_no_op_when_nothing_is_stale(session):
    user = await _make_user(session)
    session.add(AuditLog(user_id=user.id, action="auth.login", detail={}))
    await session.commit()

    assert await tasks._cleanup_old_audit_logs(retention_days=90) == {"deleted": 0}
