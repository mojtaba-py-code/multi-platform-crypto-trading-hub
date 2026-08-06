"""JWT access/refresh token issuance and verification.

Access tokens are short-lived and carry the subject (user id), role, and a
token ``type`` claim. Refresh tokens are long-lived and carry a unique ``jti``
so they can be revoked (revocation storage lives in the service layer / Redis).
"""

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.config import get_settings
from app.core.exceptions import InvalidTokenError
from app.core.logging import get_logger

log = get_logger(__name__)

TokenType = Literal["access", "refresh"]


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 0  # seconds until the access token expires


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    role: str
    token_type: TokenType
    jti: str
    expires_at: datetime


class TokenService:
    """Encode and decode JWTs using the configured secret and algorithm."""

    def __init__(
        self,
        *,
        secret: str,
        algorithm: str = "HS256",
        access_ttl: timedelta,
        refresh_ttl: timedelta,
    ) -> None:
        if not secret:
            raise InvalidTokenError("JWT secret is not configured.")
        self._secret = secret
        self._algorithm = algorithm
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    def _encode(
        self, *, subject: str, role: str, token_type: TokenType, ttl: timedelta
    ) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        expires_at = now + ttl
        payload: dict[str, Any] = {
            "sub": subject,
            "role": role,
            "type": token_type,
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return token, expires_at

    def issue_pair(self, *, subject: str, role: str) -> TokenPair:
        access, access_exp = self._encode(
            subject=subject, role=role, token_type="access", ttl=self._access_ttl
        )
        refresh, _ = self._encode(
            subject=subject, role=role, token_type="refresh", ttl=self._refresh_ttl
        )
        expires_in = int((access_exp - datetime.now(UTC)).total_seconds())
        return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def decode(self, token: str, *, expected_type: TokenType | None = None) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["exp", "sub", "type", "jti"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise InvalidTokenError("Token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError("Token is invalid.") from exc

        token_type = payload.get("type")
        if expected_type is not None and token_type != expected_type:
            raise InvalidTokenError(f"Expected a {expected_type} token.")

        return TokenClaims(
            subject=str(payload["sub"]),
            role=str(payload.get("role", "user")),
            token_type=token_type,  # type: ignore[arg-type]
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )


@functools.lru_cache(maxsize=1)
def get_token_service() -> TokenService:
    settings = get_settings()
    secret = settings.jwt_secret_key
    if not secret:
        if settings.is_production:
            raise InvalidTokenError("JWT_SECRET_KEY is required in production.")
        # Ephemeral dev secret — tokens do not survive a restart, which is fine.
        secret = uuid.uuid4().hex + uuid.uuid4().hex
        log.warning("using_ephemeral_jwt_secret", env=str(settings.app_env))
    return TokenService(
        secret=secret,
        algorithm=settings.jwt_algorithm,
        access_ttl=timedelta(minutes=settings.jwt_access_ttl_minutes),
        refresh_ttl=timedelta(days=settings.jwt_refresh_ttl_days),
    )
