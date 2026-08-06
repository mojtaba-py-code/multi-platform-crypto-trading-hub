"""Shared pytest fixtures.

Environment is configured *before* any application module is imported so the
cached settings singleton picks up the test database and deterministic keys.
"""

from __future__ import annotations

import os

# --- Test environment (must precede app imports) ---------------------------
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# Deterministic keys so tokens/ciphertext are stable within a run.
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "0FwqA3vE8m2K4rN6sT9uW1xZ3bD5gH7jK9mP1qS4uV8=")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-1234567890")
os.environ.setdefault("ALLOW_LIVE_TRADING", "false")

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from app.api.deps import get_factory  # noqa: E402
from app.database.session import get_sessionmaker, init_models, reset_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security.throttle import get_login_throttle  # noqa: E402
from app.security.token_store import get_token_store  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from tests.fakes import FakeFactory  # noqa: E402


@pytest_asyncio.fixture
async def db() -> AsyncIterator[None]:
    """Fresh in-memory schema and auth stores per test.

    The paper ledger is now durable in the database, so resetting the schema
    isolates paper state too.
    """
    await reset_engine()
    await init_models()
    # The throttle / token-store singletons are process-global; clear them so
    # a lockout or revocation in one test cannot leak into the next.
    get_login_throttle.cache_clear()
    get_token_store.cache_clear()
    yield
    await reset_engine()


@pytest_asyncio.fixture
async def session(db) -> AsyncIterator:
    maker = get_sessionmaker()
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the ASGI app, with offline market data (no network)."""
    app = create_app()
    app.dependency_overrides[get_factory] = lambda: FakeFactory(live=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def register_payload() -> dict:
    return {"email": "trader@example.com", "password": "sup3r-secret-pw"}
