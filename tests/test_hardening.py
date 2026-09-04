"""Tests for the production-hardening features:

1. Refresh-token rotation + revocation (logout).
2. Per-account brute-force lockout.
3. Durable paper ledger (balances survive across requests via the database).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from app.security.throttle import InMemoryLoginThrottle
from app.security.token_store import InMemoryTokenStore


# --- Unit: InMemoryTokenStore ----------------------------------------------
@pytest.mark.asyncio
async def test_token_store_revoke_and_expire():
    clk = {"t": 0.0}
    store = InMemoryTokenStore(clock=lambda: clk["t"])
    assert not await store.is_revoked("jti-1")
    await store.revoke("jti-1", ttl_seconds=10)
    assert await store.is_revoked("jti-1")
    clk["t"] = 11
    assert not await store.is_revoked("jti-1")  # expired → pruned


# --- Unit: InMemoryLoginThrottle --------------------------------------------
@pytest.mark.asyncio
async def test_throttle_locks_after_threshold():
    clk = {"t": 0.0}
    throttle = InMemoryLoginThrottle(max_attempts=3, lockout_seconds=100, clock=lambda: clk["t"])
    for _ in range(3):
        await throttle.record_failure("a@b.com")
    assert await throttle.is_locked("a@b.com")
    assert await throttle.retry_after("a@b.com") > 0
    clk["t"] = 101
    assert not await throttle.is_locked("a@b.com")  # cooldown elapsed


@pytest.mark.asyncio
async def test_throttle_success_resets():
    throttle = InMemoryLoginThrottle(max_attempts=3, lockout_seconds=100)
    await throttle.record_failure("a@b.com")
    await throttle.record_failure("a@b.com")
    await throttle.record_success("a@b.com")
    await throttle.record_failure("a@b.com")
    assert not await throttle.is_locked("a@b.com")  # counter was reset


@pytest.mark.asyncio
async def test_throttle_is_case_and_whitespace_insensitive():
    throttle = InMemoryLoginThrottle(max_attempts=2, lockout_seconds=100)
    await throttle.record_failure("  A@B.com ")
    await throttle.record_failure("a@b.com")
    assert await throttle.is_locked("A@B.COM")


# --- Integration helpers ----------------------------------------------------
async def _register_login(client, payload):
    await client.post("/api/v1/auth/register", json=payload)
    r = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return r.json()


@pytest_asyncio.fixture
async def headers(client, register_payload):
    tokens = await _register_login(client, register_payload)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --- Refresh rotation + logout ---------------------------------------------
@pytest.mark.asyncio
async def test_refresh_rotation_invalidates_old_token(client, register_payload):
    """Each refresh mints a new pair and retires the one presented."""
    tokens = await _register_login(client, register_payload)
    old_refresh = tokens["refresh_token"]

    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200
    new_refresh = first.json()["refresh_token"]
    assert new_refresh != old_refresh

    # The token just issued is the live one...
    live = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert live.status_code == 200

    # ...and the one it replaced is refused.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_reusing_a_retired_refresh_token_drops_every_session(client, register_payload):
    """Rotation alone does not protect the victim of a stolen token.

    Whoever presents the token first wins the race and walks off with a valid
    new pair; the other party just sees one failed refresh and logs in again,
    none the wiser. So a second use is read as proof the token leaked, and
    every outstanding session is dropped — including the pair the thief just
    minted. Without this, the theft is silent and permanent.
    """
    tokens = await _register_login(client, register_payload)
    stolen = tokens["refresh_token"]

    # The thief gets there first and receives a working pair.
    thief = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    assert thief.status_code == 200
    thief_refresh = thief.json()["refresh_token"]

    # The real user then presents the same token — the tell-tale second use.
    victim = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    assert victim.status_code == 401

    # The thief's token dies with it.
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": thief_refresh})
    assert after.status_code == 401, "the stolen session survived reuse detection"


@pytest.mark.asyncio
async def test_logging_in_again_works_after_a_session_drop(client, register_payload):
    """Revocation must not lock the legitimate user out permanently.

    A counter, not a timestamp, is what makes this safe: the new token carries
    the bumped generation, so it cannot be caught by the revocation that
    preceded it — which is exactly what one-second ``iat`` resolution would
    have done.
    """
    tokens = await _register_login(client, register_payload)
    stolen = tokens["refresh_token"]
    await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})  # triggers the drop

    fresh = await client.post("/api/v1/auth/login", json=register_payload)
    assert fresh.status_code == 200
    revived = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": fresh.json()["refresh_token"]}
    )
    assert revived.status_code == 200, "a fresh login was caught by an earlier revocation"


@pytest.mark.asyncio
async def test_logout_revokes_refresh(client, register_payload):
    tokens = await _register_login(client, register_payload)
    refresh = tokens["refresh_token"]
    logout = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert logout.status_code == 204
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert after.status_code == 401


# --- Brute-force lockout ----------------------------------------------------
@pytest.mark.asyncio
async def test_account_lockout_after_repeated_failures(client, register_payload):
    await client.post("/api/v1/auth/register", json=register_payload)
    email = register_payload["email"]
    # Default threshold is 5 failed attempts.
    for _ in range(5):
        r = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password"}
        )
        assert r.status_code == 401
    # Now even the correct password is refused with a lockout.
    locked = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": register_payload["password"]},
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_locked"
    assert "retry-after" in {k.lower() for k in locked.headers}


# --- Durable paper ledger ---------------------------------------------------
@pytest.mark.asyncio
async def test_paper_balances_persist_via_database(client, headers):
    acct = await client.post(
        "/api/v1/accounts/paper",
        headers=headers,
        json={"exchange_id": "binance", "label": "main", "market_type": "spot"},
    )
    account_id = acct.json()["id"]
    # Buy 2 BTC (FakeMarketData price = 100) → spends ~200 USDT, gains 2 BTC.
    order = await client.post(
        "/api/v1/trading/orders",
        headers=headers,
        json={
            "account_id": account_id,
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "market",
            "amount": "2",
        },
    )
    assert order.json()["status"] == "filled"

    # A *separate* request reads the portfolio — balances come from the DB, so
    # the BTC holding and the reduced USDT must be reflected.
    bal = await client.get("/api/v1/portfolio/balances", headers=headers)
    assert bal.status_code == 200
    by_asset = {b["asset"]: b for b in bal.json()}
    assert "BTC" in by_asset
    assert Decimal(by_asset["BTC"]["total"]) == Decimal(2)
    assert Decimal(by_asset["USDT"]["total"]) < Decimal(100_000)


@pytest.mark.asyncio
async def test_paper_ledger_repository_roundtrip(session):
    from app.exchange.paper_store import PaperLedger
    from app.models.account import ExchangeAccount
    from app.models.user import User
    from app.repositories.paper_ledger_repository import PaperLedgerRepository
    from app.security.crypto import get_password_hasher

    user = User(email="ledger@example.com", password_hash=get_password_hasher().hash("x" * 12))
    session.add(user)
    await session.flush()
    account = ExchangeAccount(user_id=user.id, exchange_id="binance", label="l", is_paper=True)
    session.add(account)
    await session.flush()

    repo = PaperLedgerRepository(session)
    ledger = PaperLedger(starting_balance=Decimal(5000))
    from app.exchange.models import Balance

    ledger.balances["BTC"] = Balance("BTC", Decimal("1.5"), Decimal(0))
    ledger.balances["USDT"] = Balance("USDT", Decimal(4000), Decimal(0))
    await repo.save(account.id, ledger)

    loaded = await repo.load(account.id)
    assert loaded.balances["BTC"].free == Decimal("1.5")
    assert loaded.balances["USDT"].free == Decimal(4000)
