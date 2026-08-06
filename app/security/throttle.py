"""Per-identity brute-force throttling for authentication.

Tracks consecutive failed attempts keyed by a stable identity (the login email)
and locks that identity out for a cooldown once a threshold is exceeded. This
complements the per-IP rate limiter: it defends a single account against
distributed (many-IP) password/2FA guessing.

Two backends implement the same async interface:

* :class:`InMemoryLoginThrottle` — process-local; the default for development,
  tests, and single-process deployments. A monotonic clock is injected so tests
  can advance time deterministically.
* :class:`RedisLoginThrottle` — shared across replicas, so N API processes
  cannot each grant a fresh quota of guesses for the same account, and a
  restart does not clear an active lockout.

Only the identity's hashed key and small counters are stored — never passwords.
"""

from __future__ import annotations

import functools
import hashlib
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_FAIL_PREFIX = "login_fail:"
_LOCK_PREFIX = "login_lock:"


def _normalise(identity: str) -> str:
    return identity.strip().lower()


class LoginThrottle(ABC):
    """Async interface for per-identity login throttling."""

    @abstractmethod
    async def is_locked(self, identity: str) -> bool:
        """Whether ``identity`` is currently locked out."""

    @abstractmethod
    async def retry_after(self, identity: str) -> int:
        """Seconds remaining on the lockout (0 when not locked)."""

    @abstractmethod
    async def record_failure(self, identity: str) -> None:
        """Count a failed attempt, locking out once the threshold is reached."""

    @abstractmethod
    async def record_success(self, identity: str) -> None:
        """Clear the failure counter and any lock for ``identity``."""


@dataclass
class _Entry:
    failures: int = 0
    locked_until: float = 0.0


@dataclass
class InMemoryLoginThrottle(LoginThrottle):
    max_attempts: int = 5
    lockout_seconds: int = 300
    clock: Callable[[], float] = field(default=time.monotonic)
    _entries: dict[str, _Entry] = field(default_factory=dict, init=False)

    async def is_locked(self, identity: str) -> bool:
        key = _normalise(identity)
        entry = self._entries.get(key)
        if entry is None or not entry.locked_until:
            return False
        if self.clock() < entry.locked_until:
            return True
        # Lock expired — clear it so counting restarts fresh.
        self._entries.pop(key, None)
        return False

    async def retry_after(self, identity: str) -> int:
        entry = self._entries.get(_normalise(identity))
        if entry is None or not entry.locked_until:
            return 0
        return max(0, int(entry.locked_until - self.clock()))

    async def record_failure(self, identity: str) -> None:
        entry = self._entries.setdefault(_normalise(identity), _Entry())
        entry.failures += 1
        if entry.failures >= self.max_attempts:
            entry.locked_until = self.clock() + self.lockout_seconds

    async def record_success(self, identity: str) -> None:
        self._entries.pop(_normalise(identity), None)


class RedisLoginThrottle(LoginThrottle):
    """Shared lockout state for multi-replica deployments.

    The identity is hashed before it becomes part of a Redis key so a dump of
    the keyspace does not enumerate user email addresses.
    """

    # ``client`` is injectable so the throttle can be tested without a live Redis.
    def __init__(
        self,
        redis_url: str,
        *,
        max_attempts: int = 5,
        lockout_seconds: int = 300,
        client: Any | None = None,
    ) -> None:
        if client is None:
            import redis.asyncio as redis  # imported lazily

            client = redis.from_url(redis_url, decode_responses=True)
        self._redis = client
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds

    @staticmethod
    def _digest(identity: str) -> str:
        return hashlib.sha256(_normalise(identity).encode("utf-8")).hexdigest()

    async def is_locked(self, identity: str) -> bool:
        return bool(await self._redis.exists(_LOCK_PREFIX + self._digest(identity)))

    async def retry_after(self, identity: str) -> int:
        ttl = await self._redis.ttl(_LOCK_PREFIX + self._digest(identity))
        return max(0, int(ttl)) if ttl and ttl > 0 else 0

    async def record_failure(self, identity: str) -> None:
        digest = self._digest(identity)
        fail_key = _FAIL_PREFIX + digest
        # INCR + EXPIRE in one round trip; the counter itself decays so a slow
        # trickle of failures spread over hours never accumulates into a lock.
        pipe = self._redis.pipeline()
        pipe.incr(fail_key)
        pipe.expire(fail_key, self._lockout_seconds)
        failures = (await pipe.execute())[0]
        if int(failures) >= self._max_attempts:
            await self._redis.set(_LOCK_PREFIX + digest, "1", ex=max(self._lockout_seconds, 1))

    async def record_success(self, identity: str) -> None:
        digest = self._digest(identity)
        await self._redis.delete(_FAIL_PREFIX + digest, _LOCK_PREFIX + digest)


@functools.lru_cache(maxsize=1)
def get_login_throttle() -> LoginThrottle:
    settings = get_settings()
    if settings.use_redis_auth_store:
        try:
            return RedisLoginThrottle(
                settings.redis_url,
                max_attempts=settings.login_max_attempts,
                lockout_seconds=settings.login_lockout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail boot
            log.warning("redis_login_throttle_unavailable", error=str(exc))
    return InMemoryLoginThrottle(
        max_attempts=settings.login_max_attempts,
        lockout_seconds=settings.login_lockout_seconds,
    )
