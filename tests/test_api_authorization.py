"""Every route that reaches an exchange must demand an authenticated caller.

An unauthenticated market-data route turns the service into a free, anonymous
proxy in front of the exchanges — and it is this server's IP that gets throttled
or banned when it is abused. The public surface is deliberately tiny: liveness,
readiness, and the static exchange registry.
"""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def headers(client, register_payload) -> dict:
    await client.post("/api/v1/auth/register", json=register_payload)
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
        },
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


_EXCHANGE_BACKED_ROUTES = [
    "/api/v1/market/binance/ticker?symbol=BTC/USDT",
    "/api/v1/market/binance/orderbook?symbol=BTC/USDT",
    "/api/v1/market/binance/candles?symbol=BTC/USDT&limit=10",
    "/api/v1/analytics/binance/indicator?symbol=BTC/USDT&name=rsi&limit=100",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _EXCHANGE_BACKED_ROUTES)
async def test_exchange_backed_routes_reject_anonymous_callers(client, path):
    r = await client.get(path)
    assert r.status_code in (401, 403), f"{path} is reachable without a token"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _EXCHANGE_BACKED_ROUTES)
async def test_exchange_backed_routes_work_with_a_token(client, headers, path):
    r = await client.get(path, headers=headers)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/api/v1/health", "/api/v1/ready", "/api/v1/exchanges", "/", "/dashboard"]
)
async def test_public_surface_stays_public(client, path):
    assert (await client.get(path)).status_code == 200
