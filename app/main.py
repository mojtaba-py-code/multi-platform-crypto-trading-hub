"""FastAPI application factory.

Assembles the app: settings validation, logging, middleware (request context,
security headers, CORS, rate limiting), exception handlers, the versioned API
router, and a lifespan that bootstraps the database on startup.
"""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import InMemoryRateLimiter, RequestContextMiddleware
from app.api.v1.router import api_router
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.database.session import init_models, reset_engine

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_runtime()
    # In non-production we create tables automatically; production uses Alembic.
    if not settings.is_production:
        await init_models()
    log.info(
        "application_startup",
        env=str(settings.app_env),
        live_trading=settings.allow_live_trading,
        version=__version__,
    )
    try:
        yield
    finally:
        await reset_engine()
        log.info("application_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        level="DEBUG" if settings.app_debug else "INFO",
        json_output=settings.is_production,
    )

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Multi-Platform Cryptocurrency Trading Hub — a unified, secure API to "
            "manage trading across multiple exchanges. Trading is simulated "
            "(paper) by default; live order routing is an explicit, testnet-first "
            "operator decision."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware is applied in reverse order of addition; request context should
    # be outermost so every response carries a request id and security headers.
    app.add_middleware(InMemoryRateLimiter, limit_per_minute=settings.rate_limit_per_minute)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "dashboard": "/dashboard",
            "health": f"{settings.api_v1_prefix}/health",
        }

    dashboard_template = (Path(__file__).parent / "web" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard() -> HTMLResponse:
        # The dashboard is a single self-contained file, so its CSS and JS are
        # inline — which the global ``default-src 'self'`` policy would block.
        # Rather than weaken the policy with 'unsafe-inline', mint a per-response
        # nonce and let only the two tagged blocks run. The page carries no
        # inline event-handler attributes (they cannot be nonced), so this policy
        # is strict: no external origin can load or be loaded, and XSS-injected
        # script has no way to acquire the nonce.
        nonce = secrets.token_urlsafe(16)
        html = dashboard_template.replace("{{CSP_NONCE}}", nonce)
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": (
                    f"default-src 'none'; script-src 'nonce-{nonce}'; "
                    f"style-src 'nonce-{nonce}'; connect-src 'self'; "
                    "img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors 'none'"
                )
            },
        )

    return app


app = create_app()
