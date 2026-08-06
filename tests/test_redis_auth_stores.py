"""The Redis-backed auth stores.

With more than one API replica, an in-process lockout means each replica hands
out its own fresh quota of password guesses, and a rolling restart clears every
lock. ``USE_REDIS_AUTH_STORE`` is what makes the documented behaviour true, so
the Redis backends are exercised here against a stub client rather than left to
be discovered broken in production.
"""

from __future__ import annotations

import pytest
from app.config.settings import Settings
from app.security.throttle import (
    InMemoryLoginThrottle,
    RedisLoginThrottle,
    get_login_throttle,
)
from app.security.token_store import InMemoryTokenStore, RedisTokenStore, get_token_store


class FakeRedis:
    """Just enough of ``redis.asyncio`` for these stores, with manual TTLs."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def incr(self, key: str) -> int:
        new = int(self.values.get(key, "0")) + 1
        self.values[key] = str(new)
        return new

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.values else 0

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += self.values.pop(key, None) is not None
            self.ttls.pop(key, None)
        return removed

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple] = []

    def incr(self, key: str) -> None:
        self._queued.append(("incr", (key,)))

    def expire(self, key: str, seconds: int) -> None:
        self._queued.append(("expire", (key, seconds)))

    async def execute(self) -> list:
        results = []
        for name, args in self._queued:
            results.append(await getattr(self._redis, name)(*args))
        self._queued.clear()
        return results


def _throttle(**kwargs) -> tuple[RedisLoginThrottle, FakeRedis]:
    redis = FakeRedis()
    throttle = RedisLoginThrottle(
        "redis://unused/0",
        max_attempts=kwargs.get("max_attempts", 3),
        lockout_seconds=kwargs.get("lockout_seconds", 300),
        client=redis,
    )
    return throttle, redis


def _token_store() -> tuple[RedisTokenStore, FakeRedis]:
    redis = FakeRedis()
    return RedisTokenStore("redis://unused/0", client=redis), redis


# --- RedisLoginThrottle -----------------------------------------------------
@pytest.mark.asyncio
async def test_redis_throttle_locks_after_the_threshold():
    throttle, _ = _throttle(max_attempts=3)

    for _ in range(2):
        await throttle.record_failure("a@b.com")
    assert not await throttle.is_locked("a@b.com")

    await throttle.record_failure("a@b.com")
    assert await throttle.is_locked("a@b.com")
    assert await throttle.retry_after("a@b.com") == 300


@pytest.mark.asyncio
async def test_redis_throttle_clears_everything_on_success():
    throttle, redis = _throttle(max_attempts=2)
    await throttle.record_failure("a@b.com")
    await throttle.record_failure("a@b.com")
    assert await throttle.is_locked("a@b.com")

    await throttle.record_success("a@b.com")
    assert not await throttle.is_locked("a@b.com")
    assert await throttle.retry_after("a@b.com") == 0
    assert redis.values == {}


@pytest.mark.asyncio
async def test_redis_throttle_does_not_store_the_email_in_the_clear():
    """A keyspace dump must not become a user list."""
    throttle, redis = _throttle()
    await throttle.record_failure("victim@example.com")
    assert redis.values
    assert not any("victim@example.com" in key for key in redis.values)


@pytest.mark.asyncio
async def test_redis_throttle_normalises_the_identity():
    throttle, _ = _throttle(max_attempts=2)
    await throttle.record_failure(" A@B.com ")
    await throttle.record_failure("a@b.com")
    assert await throttle.is_locked("A@B.COM")


# --- RedisTokenStore --------------------------------------------------------
@pytest.mark.asyncio
async def test_redis_token_store_revokes_with_a_ttl():
    store, redis = _token_store()

    assert not await store.is_revoked("jti-1")
    await store.revoke("jti-1", ttl_seconds=120)
    assert await store.is_revoked("jti-1")
    # The entry expires with the token, so the denylist cannot grow forever.
    assert redis.ttls["revoked_jti:jti-1"] == 120


@pytest.mark.asyncio
async def test_redis_token_store_floors_a_nonpositive_ttl():
    """An already-expired token still needs a non-zero TTL or Redis rejects it."""
    store, redis = _token_store()
    await store.revoke("jti-2", ttl_seconds=0)
    assert redis.ttls["revoked_jti:jti-2"] == 1


# --- Backend selection ------------------------------------------------------
def test_defaults_to_the_in_process_backends(monkeypatch):
    monkeypatch.setattr(
        "app.security.throttle.get_settings", lambda: Settings(use_redis_auth_store=False)
    )
    monkeypatch.setattr(
        "app.security.token_store.get_settings", lambda: Settings(use_redis_auth_store=False)
    )
    get_login_throttle.cache_clear()
    get_token_store.cache_clear()

    assert isinstance(get_login_throttle(), InMemoryLoginThrottle)
    assert isinstance(get_token_store(), InMemoryTokenStore)
    get_login_throttle.cache_clear()
    get_token_store.cache_clear()


def test_an_unreachable_redis_falls_back_instead_of_blocking_startup(monkeypatch):
    """Degrading to a process-local lockout beats refusing to serve traffic."""
    monkeypatch.setattr(
        "app.security.throttle.get_settings",
        lambda: Settings(use_redis_auth_store=True, redis_url="redis://nowhere:1/0"),
    )

    def _explode(*args, **kwargs):
        raise RuntimeError("cannot reach redis")

    monkeypatch.setattr("app.security.throttle.RedisLoginThrottle", _explode)
    get_login_throttle.cache_clear()

    assert isinstance(get_login_throttle(), InMemoryLoginThrottle)
    get_login_throttle.cache_clear()
