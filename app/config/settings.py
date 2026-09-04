"""Centralised, validated application configuration.

All configuration flows through a single :class:`Settings` object loaded from
environment variables (and an optional ``.env`` file). Nothing else in the
codebase should read ``os.environ`` directly — this keeps configuration
discoverable, typed, and testable.
"""

from __future__ import annotations

import functools
import logging
from enum import StrEnum
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

log = logging.getLogger(__name__)


class AppEnv(StrEnum):
    development = "development"
    staging = "staging"
    production = "production"


class Settings(BaseSettings):
    """Runtime configuration, validated at process start."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_env: AppEnv = AppEnv.development
    app_debug: bool = True
    app_name: str = "Crypto Trading Hub"
    api_v1_prefix: str = "/api/v1"

    # --- Security ---
    # One or more comma-separated base64 urlsafe 32-byte Fernet keys; the first
    # encrypts, all of them can decrypt (key rotation). Falls back to an
    # ephemeral key in dev/test so the app boots without configuration, but
    # production refuses to start without an explicit key (see
    # ``validate_runtime``).
    master_encryption_key: str = ""
    jwt_secret_key: str = ""
    jwt_access_ttl_minutes: int = 30
    jwt_refresh_ttl_days: int = 14
    jwt_algorithm: str = "HS256"

    # --- Trading safety ---
    allow_live_trading: bool = False
    use_exchange_testnet: bool = True

    # --- Auth hardening ---
    # Back the refresh-token denylist and login throttle with Redis when true
    # (shared across replicas); otherwise use an in-process store.
    use_redis_auth_store: bool = False
    login_max_attempts: int = 5
    login_lockout_seconds: int = 300

    # --- Database / cache ---
    database_url: str = "postgresql+asyncpg://cth:cth@localhost:5432/crypto_hub"
    redis_url: str = "redis://localhost:6379/0"

    # --- Celery ---
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- CORS / rate limiting ---
    # ``NoDecode`` stops pydantic-settings from JSON-decoding this value before
    # it reaches the validator below. Without it, the documented comma-separated
    # form (``CORS_ORIGINS=https://a.example,https://b.example``) raises at
    # startup instead of being parsed, and only a JSON array would work.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    rate_limit_per_minute: int = 120

    # --- Reverse proxy ---
    # Addresses or CIDR ranges of proxies allowed to set ``X-Forwarded-For``.
    # Empty (the default) means the header is ignored and the peer address is
    # used — correct when the app is reached directly. Set this to your proxy
    # when one is in front, or the rate limiter shares a single bucket across
    # every user and the audit log records the proxy for every login.
    # Anything listed here can claim to be any client, so list only proxies you
    # operate. See ``app/api/client_address.py``.
    trusted_proxy_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", "trusted_proxy_ips", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        """Accept either a comma-separated string or an already-parsed list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.production

    @property
    def encryption_keys(self) -> list[str]:
        """Fernet keys in priority order; the first is the active (encrypting) key."""
        return [k.strip() for k in self.master_encryption_key.split(",") if k.strip()]

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN (used by Alembic migrations)."""
        return self.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")

    def validate_runtime(self) -> None:
        """Fail fast on unsafe production configuration.

        Called explicitly during application startup rather than at import time
        so that tests and tooling can construct settings freely.
        """
        if not self.is_production:
            return
        missing: list[str] = []
        if not self.encryption_keys:
            missing.append("MASTER_ENCRYPTION_KEY")
        if not self.jwt_secret_key or len(self.jwt_secret_key) < 32:
            missing.append("JWT_SECRET_KEY (>= 32 chars)")
        if self.app_debug:
            missing.append("APP_DEBUG must be false in production")
        # A wildcard CORS origin combined with credentialed requests is unsafe;
        # the app sends credentials, so reject "*".
        if "*" in self.cors_origins:
            missing.append("CORS_ORIGINS must not be '*' in production")
        if self.allow_live_trading and not self.use_exchange_testnet:
            log.warning("live_trading_on_mainnet_enabled")
        if missing:
            raise RuntimeError(
                "Refusing to start in production with unsafe configuration: " + ", ".join(missing)
            )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
