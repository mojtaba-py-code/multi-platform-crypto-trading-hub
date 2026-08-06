"""Tests for the circuit breaker and retry helper."""

from __future__ import annotations

import pytest
from app.core.exceptions import CircuitOpenError
from app.core.resilience import CircuitBreaker, CircuitState, async_retry


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def _boom() -> None:
    raise ValueError("boom")


async def _ok() -> str:
    return "ok"


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold():
    clock = _Clock()
    cb = CircuitBreaker(name="t", failure_threshold=3, recovery_timeout=10, clock=clock)
    for _ in range(3):
        with pytest.raises(ValueError):
            await cb.call(_boom)
    assert cb.state is CircuitState.open
    # Further calls are rejected fast without invoking the function.
    with pytest.raises(CircuitOpenError):
        await cb.call(_ok)


@pytest.mark.asyncio
async def test_circuit_half_open_recovers():
    clock = _Clock()
    cb = CircuitBreaker(name="t", failure_threshold=1, recovery_timeout=5, clock=clock)
    with pytest.raises(ValueError):
        await cb.call(_boom)
    assert cb.state is CircuitState.open
    clock.t = 6  # past recovery timeout
    result = await cb.call(_ok)  # probe succeeds
    assert result == "ok"
    assert cb.state is CircuitState.closed


@pytest.mark.asyncio
async def test_retry_eventually_succeeds():
    calls = {"n": 0}

    async def _sleep(_seconds: float) -> None:
        return None

    @async_retry(attempts=3, sleep=_sleep)
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "done"

    assert await flaky() == "done"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retry_exhausts_and_raises():
    async def _sleep(_seconds: float) -> None:
        return None

    @async_retry(attempts=2, sleep=_sleep)
    async def always_fail() -> None:
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        await always_fail()
