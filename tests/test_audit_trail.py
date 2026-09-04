"""Security events survive the request that was rejected.

The request-scoped session rolls back whenever a handler raises. That is right
for business writes and wrong for the audit trail, because every event an
investigator actually wants — a failed login, a lockout, a reused refresh
token — is recorded on a path that ends in an exception.

Left alone, the log fills with successful logins and contains no trace of the
attempts that failed, which is the exact inverse of what it is for. Nothing
crashes and no test that only checks status codes notices, so these assert on
the persisted rows.
"""

from __future__ import annotations

import pytest
from app.database.session import get_sessionmaker
from app.models.audit import AuditLog
from sqlalchemy import select


async def _recorded_actions() -> list[str]:
    """Audit actions visible from a *fresh* session — i.e. actually committed."""
    async with get_sessionmaker()() as session:
        return list((await session.execute(select(AuditLog.action))).scalars().all())


async def _register(client, payload) -> None:
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201


@pytest.mark.asyncio
async def test_a_successful_login_is_recorded(client, register_payload):
    """The baseline: this one always worked, because nothing raised."""
    await _register(client, register_payload)
    assert (await client.post("/api/v1/auth/login", json=register_payload)).status_code == 200
    assert "auth.login" in await _recorded_actions()


@pytest.mark.asyncio
async def test_a_failed_login_is_recorded(client, register_payload):
    """Without an explicit commit this row is written and then rolled back."""
    await _register(client, register_payload)
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": register_payload["email"], "password": "not-the-password"},
    )
    assert bad.status_code == 401
    assert "auth.login_failed" in await _recorded_actions()


@pytest.mark.asyncio
async def test_a_login_for_an_unknown_email_is_recorded(client):
    """An attacker probing for valid accounts must leave a trail."""
    probe = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "guessing"}
    )
    assert probe.status_code == 401
    assert "auth.login_failed" in await _recorded_actions()


@pytest.mark.asyncio
async def test_a_lockout_is_recorded(client, register_payload):
    """The moment an account is locked is the one an investigator looks for."""
    await _register(client, register_payload)
    for _ in range(5):  # the default threshold
        await client.post(
            "/api/v1/auth/login",
            json={"email": register_payload["email"], "password": "wrong"},
        )
    locked = await client.post("/api/v1/auth/login", json=register_payload)
    assert locked.status_code == 429

    actions = await _recorded_actions()
    assert "auth.locked_out" in actions
    assert actions.count("auth.login_failed") == 5


@pytest.mark.asyncio
async def test_a_login_to_a_disabled_account_is_recorded(client, register_payload, session):
    """Correct credentials against a revoked account is worth knowing about."""
    from app.models.user import User

    await _register(client, register_payload)
    user = (await session.execute(select(User))).scalars().one()
    user.is_active = False
    await session.commit()

    refused = await client.post("/api/v1/auth/login", json=register_payload)
    assert refused.status_code == 401
    assert "auth.login_disabled" in await _recorded_actions()


@pytest.mark.asyncio
async def test_refresh_token_reuse_is_recorded(client, register_payload):
    """The revocation it triggers has to outlive the rejection as well."""
    await _register(client, register_payload)
    tokens = (await client.post("/api/v1/auth/login", json=register_payload)).json()
    stolen = tokens["refresh_token"]

    await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    assert replay.status_code == 401

    assert "auth.sessions_invalidated" in await _recorded_actions()


@pytest.mark.asyncio
async def test_the_audit_trail_never_stores_the_submitted_password(client, register_payload):
    """A log of failed attempts is a log of near-miss passwords if it leaks."""
    await _register(client, register_payload)
    await client.post(
        "/api/v1/auth/login",
        json={"email": register_payload["email"], "password": "hunter2-almost-right"},
    )
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    blob = " ".join(f"{r.action}{r.detail}{r.ip_address}" for r in rows)
    assert "hunter2" not in blob
    assert register_payload["password"] not in blob
