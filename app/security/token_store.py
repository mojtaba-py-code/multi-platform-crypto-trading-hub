"""Revocation store for JWT identifiers (``jti``).

Used to make refresh tokens one-time-use (rotation) and revocable on logout.
Two backends implement the same async interface:

* :class:`InMemoryTokenStore` — process-local; the safe default for development
  and tests. Entries carry an expiry so the map self-prunes.
* :class:`RedisTokenStore` — shared across replicas for production.

Only opaque token identifiers and expiries are stored — never token contents.
"""

from __future__ import annotations

import functools
import time
from abc import ABC, abstractmethod
from typing import Any

from app.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_PREFIX = "revoked_jti:"


class TokenStore(ABC):
    @abstractmethod
    async def revoke(self, jti: str, *, ttl_seconds: int) -> None:
        """Mark ``jti`` revoked for at least ``ttl_seconds`` (its remaining life)."""

    @abstractmethod
    async def is_revoked(self, jti: str) -> bool: ...


class InMemoryTokenStore(TokenStore):
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._revoked: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        expired = [jti for jti, exp in self._revoked.items() if exp <= now]
        for jti in expired:
            del self._revoked[jti]

    async def revoke(self, jti: str, *, ttl_seconds: int) -> None:
        now = self._clock()
        self._prune(now)
        self._revoked[jti] = now + max(ttl_seconds, 1)

    async def is_revoked(self, jti: str) -> bool:
        now = self._clock()
        exp = self._revoked.get(jti)
        if exp is None:
            return False
        if exp <= now:
            del self._revoked[jti]
            return False
        return True


class RedisTokenStore(TokenStore):
    # ``client`` is injectable so the store can be tested without a live Redis.
    def __init__(self, redis_url: str, *, client: Any | None = None) -> None:
        if client is None:
            import redis.asyncio as redis  # imported lazily

            client = redis.from_url(redis_url, decode_responses=True)
        self._redis = client

    async def revoke(self, jti: str, *, ttl_seconds: int) -> None:
        await self._redis.set(_PREFIX + jti, "1", ex=max(ttl_seconds, 1))

    async def is_revoked(self, jti: str) -> bool:
        return bool(await self._redis.exists(_PREFIX + jti))


@functools.lru_cache(maxsize=1)
def get_token_store() -> TokenStore:
    settings = get_settings()
    if settings.use_redis_auth_store:
        try:
            return RedisTokenStore(settings.redis_url)
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail boot
            log.warning("redis_token_store_unavailable", error=str(exc))
    return InMemoryTokenStore()
