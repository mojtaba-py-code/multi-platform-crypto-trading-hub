"""The per-IP rate limiter, exercised through a real ASGI stack.

`test_client_address.py` pins the address resolution in isolation; this file
proves the limiter actually uses it. The bug being guarded against is not a
crash — it is the limiter quietly keying every request in the deployment on the
proxy's address, which turns a per-client quota into one global quota. That
looks fine in development (no proxy, so it works) and only fails in production,
under load, as a self-inflicted outage.
"""

from __future__ import annotations

import pytest
from app.api.middleware import InMemoryRateLimiter
from app.config.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

_LIMIT = 3
_HEALTH = "/api/v1/health"


def _app(**kwargs) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        InMemoryRateLimiter, limit_per_minute=_LIMIT, exempt_paths=(_HEALTH,), **kwargs
    )

    @app.get("/thing")
    async def thing() -> dict:
        return {"ok": True}

    @app.get(_HEALTH)
    async def health() -> dict:
        return {"status": "ok"}

    return app


def _client(app: FastAPI) -> AsyncClient:
    # ASGITransport reports 127.0.0.1 as the peer, standing in for the proxy.
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _trust_loopback(monkeypatch, trusted: list[str]) -> None:
    monkeypatch.setattr(
        "app.api.client_address.get_settings",
        lambda: Settings(_env_file=None, trusted_proxy_ips=trusted),
    )


@pytest.mark.asyncio
async def test_one_client_is_limited_after_its_quota():
    app = _app()
    async with _client(app) as c:
        for _ in range(_LIMIT):
            assert (await c.get("/thing")).status_code == 200
        blocked = await c.get("/thing")
    assert blocked.status_code == 429
    # A client that is told to back off needs to know for how long.
    assert int(blocked.headers["Retry-After"]) >= 0


@pytest.mark.asyncio
async def test_behind_a_trusted_proxy_each_client_gets_its_own_quota(monkeypatch):
    """The actual regression: without this, all users share one bucket.

    Two callers arrive through the same proxy. Exhausting the first one's quota
    must leave the second untouched.
    """
    _trust_loopback(monkeypatch, ["127.0.0.1"])
    app = _app()
    async with _client(app) as c:
        for _ in range(_LIMIT):
            spent = await c.get("/thing", headers={"X-Forwarded-For": "203.0.113.1"})
            assert spent.status_code == 200
        exhausted = await c.get("/thing", headers={"X-Forwarded-For": "203.0.113.1"})
        other = await c.get("/thing", headers={"X-Forwarded-For": "203.0.113.2"})

    assert exhausted.status_code == 429
    assert other.status_code == 200, "a second client inherited the first client's quota"


@pytest.mark.asyncio
async def test_without_a_trusted_proxy_a_forged_header_buys_nothing(monkeypatch):
    """Rotating X-Forwarded-For must not reset the quota when nobody is trusted.

    Otherwise the limiter is trivially bypassed by anyone who can set a header.
    """
    _trust_loopback(monkeypatch, [])
    app = _app()
    async with _client(app) as c:
        for i in range(_LIMIT):
            spent = await c.get("/thing", headers={"X-Forwarded-For": f"203.0.113.{i}"})
            assert spent.status_code == 200
        blocked = await c.get("/thing", headers={"X-Forwarded-For": "203.0.113.99"})
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_health_probes_are_never_throttled():
    """A throttled liveness probe gets the container killed under load."""
    app = _app()
    async with _client(app) as c:
        for _ in range(_LIMIT * 3):
            assert (await c.get(_HEALTH)).status_code == 200


@pytest.mark.asyncio
async def test_the_exempt_paths_follow_the_configured_api_prefix(monkeypatch):
    """The prefix is configurable; the exemption has to be derived from it.

    A hardcoded '/api/v1/health' stops matching the moment someone sets a
    different prefix, and the orchestrator's liveness probe starts collecting
    429s — which gets the container restarted under exactly the load that
    caused it.
    """
    from app.main import create_app

    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(_env_file=None, api_v1_prefix="/custom/v9", rate_limit_per_minute=_LIMIT),
    )
    app = create_app()
    async with _client(app) as c:
        for _ in range(_LIMIT * 3):
            assert (await c.get("/custom/v9/health")).status_code == 200
        # A non-exempt path under the same prefix is still limited.
        for _ in range(_LIMIT):
            await c.get("/custom/v9/market/exchanges")
        assert (await c.get("/custom/v9/market/exchanges")).status_code == 429
