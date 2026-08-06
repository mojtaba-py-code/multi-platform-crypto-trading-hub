"""System endpoints and RBAC enforcement at the API boundary."""

from __future__ import annotations

import pytest
from app.security.tokens import get_token_service


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_reports_safe_defaults(client):
    r = await client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["live_trading_enabled"] is False  # safe by default


@pytest.mark.asyncio
async def test_exchanges_listing(client):
    r = await client.get("/api/v1/exchanges")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["exchanges"]}
    assert {"binance", "bybit", "okx", "kucoin", "kraken"} <= ids


@pytest.mark.asyncio
async def test_security_headers_present(client):
    r = await client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "x-request-id" in r.headers


@pytest.mark.asyncio
async def test_viewer_cannot_place_order(client):
    # Issue a token for the read-only "viewer" role directly.
    tokens = get_token_service()
    pair = tokens.issue_pair(subject="viewer-user", role="viewer")
    headers = {"Authorization": f"Bearer {pair.access_token}"}
    r = await client.post(
        "/api/v1/trading/orders",
        headers=headers,
        json={
            "account_id": "x",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "market",
            "amount": "1",
        },
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_invalid_token_rejected(client):
    r = await client.get("/api/v1/accounts", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
