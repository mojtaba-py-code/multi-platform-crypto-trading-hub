"""Custom ASGI middleware: request IDs, security headers, and rate limiting."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.client_address import resolve_client_ip
from app.core.exceptions import RateLimitError

log = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, bind logging context, and add security headers."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(
            request_id=request_id, path=request.url.path, method=request.method
        )
        start = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = str(elapsed_ms)
        _apply_security_headers(response)
        return response


def _apply_security_headers(response: Response) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"
    )
    response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")


class InMemoryRateLimiter(BaseHTTPMiddleware):
    """A simple fixed-window-ish sliding rate limiter keyed by client IP.

    Suitable for a single process / development. In production a Redis-backed
    limiter should be used across replicas; this keeps the dependency optional
    while still demonstrating the control.
    """

    def __init__(
        self, app, *, limit_per_minute: int = 120, exempt_paths: Iterable[str] | None = None
    ) -> None:
        super().__init__(app)
        self._limit = limit_per_minute
        self._window = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_sweep = 0.0
        # Paths that must never be rate limited (health checks / probes). The
        # API prefix is configurable, so these are passed in rather than
        # hardcoded — otherwise changing the prefix silently starts throttling
        # the orchestrator's liveness probe.
        self._exempt = set(exempt_paths or ())

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._exempt:
            return await call_next(request)
        # The peer address is the proxy's when one is in front; without this
        # every user would share a single bucket. See ``client_address``.
        client = resolve_client_ip(request) or "unknown"
        now = time.monotonic()
        bucket = self._hits[client]
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        if len(bucket) >= self._limit:
            err = RateLimitError()
            retry_after = max(0, int(self._window - (now - bucket[0]))) if bucket else 60
            return JSONResponse(
                err.to_dict(),
                status_code=err.status_code,
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        self._maybe_sweep(now)
        return await call_next(request)

    def _maybe_sweep(self, now: float) -> None:
        """Periodically drop idle IP buckets so the map cannot grow unbounded."""
        if now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        stale = [ip for ip, hits in self._hits.items() if not hits or now - hits[-1] > self._window]
        for ip in stale:
            del self._hits[ip]
