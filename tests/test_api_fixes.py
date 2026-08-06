"""API-level regression tests for audit fixes (cancel, new endpoints, validation)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from tests.totp_helpers import totp_next, totp_now


async def _auth_headers(client, payload) -> dict:
    await client.post("/api/v1/auth/register", json=payload)
    login = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest_asyncio.fixture
async def headers(client, register_payload) -> dict:
    return await _auth_headers(client, register_payload)


async def _paper_account(client, headers) -> str:
    r = await client.post(
        "/api/v1/accounts/paper",
        headers=headers,
        json={"exchange_id": "binance", "label": "main", "market_type": "spot"},
    )
    return r.json()["id"]


# --- /auth/me ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_endpoint(client, headers, register_payload):
    r = await client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == register_payload["email"]


# --- account get-by-id ------------------------------------------------------
@pytest.mark.asyncio
async def test_get_account_by_id(client, headers):
    account_id = await _paper_account(client, headers)
    r = await client.get(f"/api/v1/accounts/{account_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == account_id


@pytest.mark.asyncio
async def test_get_missing_account_404(client, headers):
    r = await client.get("/api/v1/accounts/does-not-exist", headers=headers)
    assert r.status_code == 404


# --- single order fetch + cancel persists (arch #1, #2) ---------------------
@pytest.mark.asyncio
async def test_cancel_resting_limit_order_persists(client, headers):
    account_id = await _paper_account(client, headers)
    # A non-marketable limit buy rests as "open".
    place = await client.post(
        "/api/v1/trading/orders",
        headers=headers,
        json={
            "account_id": account_id,
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "1",
            "price": "50",  # below market (FakeMarketData last=100) → rests
        },
    )
    assert place.status_code == 200, place.text
    order_id = place.json()["id"]
    assert place.json()["status"] == "open"

    # Cancel by record id.
    cancel = await client.delete(f"/api/v1/trading/orders/{order_id}", headers=headers)
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "canceled"
    assert cancel.json()["id"] == order_id  # consistent shape (record id)

    # The persisted record reflects the cancellation.
    fetched = await client.get(f"/api/v1/trading/orders/{order_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "canceled"


@pytest.mark.asyncio
async def test_cancel_unknown_order_404(client, headers):
    r = await client.delete("/api/v1/trading/orders/nope", headers=headers)
    assert r.status_code == 404


# --- paper sell works across requests (correctness BUG1) --------------------
@pytest.mark.asyncio
async def test_paper_buy_then_sell_via_api(client, headers):
    account_id = await _paper_account(client, headers)

    async def market(side, amount):
        return await client.post(
            "/api/v1/trading/orders",
            headers=headers,
            json={
                "account_id": account_id,
                "symbol": "BTC/USDT",
                "side": side,
                "type": "market",
                "amount": amount,
            },
        )

    assert (await market("buy", "2")).json()["status"] == "filled"
    sell = await market("sell", "1")
    assert sell.status_code == 200, sell.text
    assert sell.json()["status"] == "filled"  # base balance persisted across requests


# --- positions & open-orders endpoints exist -------------------------------
@pytest.mark.asyncio
async def test_positions_and_open_orders_endpoints(client, headers):
    account_id = await _paper_account(client, headers)
    pos = await client.get(f"/api/v1/trading/accounts/{account_id}/positions", headers=headers)
    assert pos.status_code == 200
    assert pos.json() == []  # spot paper has no positions
    oo = await client.get(f"/api/v1/trading/accounts/{account_id}/open-orders", headers=headers)
    assert oo.status_code == 200


# --- input validation (security H1) ----------------------------------------
@pytest.mark.asyncio
async def test_malicious_symbol_rejected(client, headers):
    account_id = await _paper_account(client, headers)
    r = await client.post(
        "/api/v1/trading/orders",
        headers=headers,
        json={
            "account_id": account_id,
            "symbol": "<img src=x onerror=alert(1)>",
            "side": "buy",
            "type": "market",
            "amount": "1",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_malicious_account_label_rejected(client, headers):
    r = await client.post(
        "/api/v1/accounts/paper",
        headers=headers,
        json={"exchange_id": "binance", "label": "<script>x</script>", "market_type": "spot"},
    )
    assert r.status_code == 422


# --- indicator endpoint now exposes all advertised indicators ---------------
@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["macd", "bollinger", "stochastic", "supertrend"])
async def test_extended_indicators_reachable(client, headers, name):
    r = await client.get(
        f"/api/v1/analytics/binance/indicator?symbol=BTC/USDT&name={name}&limit=100",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["indicator"] == name


@pytest.mark.asyncio
async def test_bad_timeframe_rejected(client, headers):
    r = await client.get(
        "/api/v1/analytics/binance/indicator?symbol=BTC/USDT&name=rsi&timeframe=99z",
        headers=headers,
    )
    assert r.status_code == 422


# --- 2FA disable ------------------------------------------------------------
@pytest.mark.asyncio
async def test_2fa_enroll_confirm_disable(client, headers):
    enroll = await client.post("/api/v1/auth/2fa/enroll", headers=headers)
    secret = enroll.json()["secret"]
    await client.post("/api/v1/auth/2fa/confirm", headers=headers, json={"code": totp_now(secret)})
    # A *different* time step: each code is single-use, so replaying the one the
    # confirmation consumed would (correctly) be rejected.
    disable = await client.post(
        "/api/v1/auth/2fa/disable", headers=headers, json={"code": totp_next(secret)}
    )
    assert disable.status_code == 204
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["totp_enabled"] is False


# --- login for unknown user is a clean 401 (M3) -----------------------------
@pytest.mark.asyncio
async def test_login_unknown_user(client):
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@nowhere.com", "password": "whatever-password"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_error"
