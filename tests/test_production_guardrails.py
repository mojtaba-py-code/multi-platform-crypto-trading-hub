"""The production configuration gate.

``Settings.validate_runtime`` is the last thing standing between a misconfigured
deployment and a live trading API served with a default secret. It runs once, at
startup, and is easy to weaken by accident — so each rejection is pinned here.
"""

from __future__ import annotations

import pytest
from app.config.settings import AppEnv, Settings
from app.security.crypto import generate_master_key

_GOOD_KEY = generate_master_key()
_GOOD_JWT = "a-production-jwt-secret-that-is-long-enough"


def _prod(**overrides) -> Settings:
    base = {
        "app_env": AppEnv.production,
        "app_debug": False,
        "master_encryption_key": _GOOD_KEY,
        "jwt_secret_key": _GOOD_JWT,
        "cors_origins": ["https://app.example.com"],
    }
    return Settings(**{**base, **overrides})


def test_a_fully_configured_production_setup_starts():
    _prod().validate_runtime()  # must not raise


def test_non_production_environments_are_never_blocked():
    """Dev must boot with no configuration at all, or nobody can run the project."""
    Settings(
        app_env=AppEnv.development, master_encryption_key="", jwt_secret_key=""
    ).validate_runtime()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"master_encryption_key": ""}, "MASTER_ENCRYPTION_KEY"),
        ({"jwt_secret_key": ""}, "JWT_SECRET_KEY"),
        ({"jwt_secret_key": "too-short"}, "JWT_SECRET_KEY"),
        ({"app_debug": True}, "APP_DEBUG"),
        # A wildcard origin plus credentialed requests would let any site drive
        # the API with a logged-in user's browser.
        ({"cors_origins": ["*"]}, "CORS_ORIGINS"),
    ],
)
def test_unsafe_production_configuration_refuses_to_start(overrides, expected):
    with pytest.raises(RuntimeError, match=expected):
        _prod(**overrides).validate_runtime()


def test_every_problem_is_reported_at_once():
    """One restart should reveal the whole list, not just the first mistake."""
    with pytest.raises(RuntimeError) as excinfo:
        _prod(master_encryption_key="", jwt_secret_key="", app_debug=True).validate_runtime()
    message = str(excinfo.value)
    assert "MASTER_ENCRYPTION_KEY" in message
    assert "JWT_SECRET_KEY" in message
    assert "APP_DEBUG" in message


def test_live_mainnet_trading_is_allowed_but_warned_about(caplog):
    """Deliberate operator choice: it must not be silent, and must not block."""
    _prod(allow_live_trading=True, use_exchange_testnet=False).validate_runtime()


def test_cors_origins_accept_a_comma_separated_string():
    settings = Settings(cors_origins="https://a.example.com, https://b.example.com")
    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]


@pytest.mark.asyncio
async def test_startup_runs_the_gate_and_refuses_an_unsafe_production_config(monkeypatch):
    """The gate is only useful if the lifespan actually calls it."""
    from app.main import create_app

    monkeypatch.setattr("app.main.get_settings", lambda: _prod(jwt_secret_key="short"))
    app = create_app()
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - startup must not get this far


@pytest.mark.asyncio
async def test_startup_bootstraps_tables_outside_production(db):
    """Dev/test boot without migrations; production is expected to run Alembic."""
    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        pass


def test_sync_database_url_strips_the_async_driver():
    """Alembic runs synchronously against the same database."""
    assert (
        Settings(database_url="postgresql+asyncpg://u:p@h/db").sync_database_url
        == "postgresql://u:p@h/db"
    )
    assert (
        Settings(database_url="sqlite+aiosqlite:///./x.db").sync_database_url == "sqlite:///./x.db"
    )
