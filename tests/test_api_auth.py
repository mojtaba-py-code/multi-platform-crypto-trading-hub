"""Integration tests for the authentication API."""

from __future__ import annotations

import pytest
from tests.totp_helpers import totp_next, totp_now


@pytest.mark.asyncio
async def test_register_and_login(client, register_payload):
    r = await client.post("/api/v1/auth/register", json=register_payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == register_payload["email"]
    assert body["role"] == "trader"
    assert "password" not in body

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": register_payload["email"], "password": register_payload["password"]},
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_duplicate_registration_conflicts(client, register_payload):
    await client.post("/api/v1/auth/register", json=register_payload)
    r = await client.post("/api/v1/auth/register", json=register_payload)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_login_wrong_password(client, register_payload):
    await client.post("/api/v1/auth/register", json=register_payload)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": register_payload["email"], "password": "totally-wrong-pw"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_error"


@pytest.mark.asyncio
async def test_weak_password_rejected(client):
    r = await client.post("/api/v1/auth/register", json={"email": "a@b.com", "password": "short"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_refresh_flow(client, register_payload):
    await client.post("/api/v1/auth/register", json=register_payload)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": register_payload["email"], "password": register_payload["password"]},
    )
    refresh_token = login.json()["refresh_token"]
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r.status_code == 200
    assert r.json()["access_token"]


@pytest.mark.asyncio
async def test_two_factor_enrollment_and_enforcement(client, register_payload):
    await client.post("/api/v1/auth/register", json=register_payload)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": register_payload["email"], "password": register_payload["password"]},
    )
    access = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    enroll = await client.post("/api/v1/auth/2fa/enroll", headers=headers)
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    assert enroll.json()["provisioning_uri"].startswith("otpauth://")

    confirm = await client.post(
        "/api/v1/auth/2fa/confirm", headers=headers, json={"code": totp_now(secret)}
    )
    assert confirm.status_code == 204

    # Re-enrolling while 2FA is live must be refused — otherwise a stolen access
    # token alone could swap the second factor.
    reenroll = await client.post("/api/v1/auth/2fa/enroll", headers=headers)
    assert reenroll.status_code == 409

    # Now login without a code must fail with two_factor_required.
    no_code = await client.post(
        "/api/v1/auth/login",
        json={"email": register_payload["email"], "password": register_payload["password"]},
    )
    assert no_code.status_code == 401
    assert no_code.json()["error"]["code"] == "two_factor_required"

    # Login with a valid, not-yet-used code succeeds.
    fresh_code = totp_next(secret)
    with_code = await client.post(
        "/api/v1/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
            "totp_code": fresh_code,
        },
    )
    assert with_code.status_code == 200

    # Replaying that same code must not authenticate a second time.
    replay = await client.post(
        "/api/v1/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
            "totp_code": fresh_code,
        },
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "authentication_error"


@pytest.mark.asyncio
async def test_protected_route_requires_token(client):
    r = await client.get("/api/v1/accounts")
    assert r.status_code in (401, 403)
