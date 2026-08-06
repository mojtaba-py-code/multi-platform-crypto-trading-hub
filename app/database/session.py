"""Async engine and session management.

The engine is created lazily from settings and cached per event loop. Tests can
call :func:`reset_engine` to drop the cached engine and point at an in-memory
SQLite database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database.base import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        connect_args: dict = {}
        engine_kwargs: dict = {"echo": False, "future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            if ":memory:" in url:
                # A single shared connection so tables created on one connection
                # are visible to every session (used by the test suite).
                engine_kwargs["poolclass"] = StaticPool
                engine_kwargs.pop("pool_pre_ping", None)
        _engine = create_async_engine(url, connect_args=connect_args, **engine_kwargs)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency and unit-of-work boundary.

    Commits when the request handler returns normally, rolls back on any
    exception, and always closes the session. Services therefore only need to
    ``flush``; the request owns the transaction commit.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def init_models() -> None:
    """Create all tables (used for tests and first-run bootstrapping)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def reset_engine() -> None:
    """Dispose the cached engine (tests use this to swap databases)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
