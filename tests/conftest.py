"""Shared pytest fixtures.

Environment is configured *before* any application module is imported so the
cached settings singleton picks up the test database and the generated keys.
"""

from __future__ import annotations

import os
import secrets

from cryptography.fernet import Fernet

# --- Test environment (must precede app imports) ---------------------------
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# Keys are generated once per session, here, before the settings singleton is
# built — so they are fixed for the whole run (ciphertext written by one test is
# readable by the next) without a key-shaped literal ever entering the
# repository. A committed key is indistinguishable from a leaked one to a
# scanner, and copy-pasting one into a real .env is a mistake worth designing
# out. ``cryptography`` and ``secrets`` are safe to import here; an ``app``
# import would cache the settings before the environment is ready.
os.environ.setdefault("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
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
