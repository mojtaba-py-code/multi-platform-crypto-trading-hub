"""Resilience primitives: a circuit breaker and retry helpers.

External calls to exchanges are unreliable — networks flap and exchanges rate
limit or go into maintenance. These utilities keep a misbehaving exchange from
taking down the whole service.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ParamSpec, TypeVar

from app.core.exceptions import CircuitOpenError
from app.core.logging import get_logger

log = get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


class CircuitState(StrEnum):
    closed = "closed"  # healthy, calls pass through
    open = "open"  # failing, calls are rejected immediately
    half_open = "half_open"  # probing whether the dependency recovered


@dataclass
class CircuitBreaker:
    """A minimal async circuit breaker.

    After ``failure_threshold`` consecutive failures the circuit opens and
    rejects calls for ``recovery_timeout`` seconds. It then allows a single
    probe (``half_open``); success closes the circuit, failure re-opens it.

    A monotonic clock is injected for deterministic testing.
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    clock: Callable[[], float] = field(default=time.monotonic)

    _state: CircuitState = field(default=CircuitState.closed, init=False)
    _failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    # True while a single half-open probe is in flight, so concurrent callers
    # cannot all slip through the half-open gate at once.
    _probe_in_flight: bool = field(default=False, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    def _can_attempt(self) -> bool:
        if self._state is CircuitState.closed:
            return True
        if self._state is CircuitState.open:
            if self.clock() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.half_open
                self._probe_in_flight = True
                return True
            return False
        # half_open: admit exactly one probe at a time.
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.closed
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._failures += 1
        self._probe_in_flight = False
        if self._state is CircuitState.half_open or self._failures >= self.failure_threshold:
            self._state = CircuitState.open
            self._opened_at = self.clock()
            log.warning("circuit_opened", circuit=self.name, failures=self._failures)

    async def call(self, func: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs) -> T:
        async with self._lock:
            if not self._can_attempt():
                raise CircuitOpenError(details={"circuit": self.name})
        try:
            result = await func(*args, **kwargs)
        except Exception:
            async with self._lock:
                self.record_failure()
            raise
        else:
            async with self._lock:
                self.record_success()
            return result


# Shared breakers so state persists across the short-lived adapter instances
# that the exchange factory creates per request. Keyed by a stable name
# (e.g. the exchange id), a single breaker guards every call to that dependency.
_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str, *, failure_threshold: int = 5, recovery_timeout: float = 30.0
) -> CircuitBreaker:
    breaker = _breakers.get(name)
    if breaker is None:
        breaker = CircuitBreaker(
            name=name, failure_threshold=failure_threshold, recovery_timeout=recovery_timeout
        )
        _breakers[name] = breaker
    return breaker


def reset_circuit_breakers() -> None:
    """Clear the shared breaker registry (used by tests)."""
    _breakers.clear()


def async_retry(
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 5.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Retry an async callable with exponential backoff.

    ``sleep`` is injectable so tests can run without real delays.
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt == attempts:
                        break
                    log.info(
                        "retrying",
                        func=func.__name__,
                        attempt=attempt,
                        max_attempts=attempts,
                        error=str(exc),
                    )
                    await sleep(min(delay, max_delay))
                    delay *= 2
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
