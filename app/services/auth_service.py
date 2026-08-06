"""Authentication and user-lifecycle business logic."""

from __future__ import annotations

import functools
from datetime import UTC, datetime

import anyio.to_thread

from app.core.exceptions import (
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    TwoFactorRequiredError,
)
from app.core.logging import get_logger
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.repositories.user_repository import UserRepository
from app.security.crypto import PasswordHasher, SecretCipher
from app.security.rbac import Role
from app.security.throttle import LoginThrottle
from app.security.token_store import TokenStore
from app.security.tokens import TokenPair, TokenService
from app.security.totp import TotpService

log = get_logger(__name__)


def _remaining_ttl(expires_at: datetime) -> int:
    return max(1, int((expires_at - datetime.now(UTC)).total_seconds()))


@functools.lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """A cached, valid Argon2 hash used to equalise timing for unknown users."""
    return PasswordHasher().hash("decoy-password-for-constant-time-auth")


class AuthService:
    def __init__(
        self,
        *,
        users: UserRepository,
        audit: AuditRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
        totp: TotpService,
        cipher: SecretCipher,
        token_store: TokenStore,
        throttle: LoginThrottle,
    ) -> None:
        self._users = users
        self._audit = audit
        self._hasher = hasher
        self._tokens = tokens
        self._totp = totp
        self._cipher = cipher
        self._token_store = token_store
        self._throttle = throttle

    async def register(self, *, email: str, password: str) -> User:
        email = email.lower()
        if await self._users.email_exists(email):
            raise ConflictError("A user with this email already exists.")
        user = User(
            email=email,
            password_hash=await self._hasher.hash_async(password),
            role=Role.trader.value,
        )
        await self._users.add(user)
        await self._audit.record(action="user.register", user_id=user.id)
        log.info("user_registered", user_id=user.id)
        return user

    async def authenticate(
        self, *, email: str, password: str, totp_code: str | None, ip: str | None = None
    ) -> TokenPair:
        email = email.lower()
        # Per-account lockout: reject early while the identity is locked out so a
        # distributed guessing campaign cannot keep trying a single account.
        if await self._throttle.is_locked(email):
            await self._audit.record(action="auth.locked_out", ip_address=ip)
            raise AccountLockedError(
                details={"retry_after": await self._throttle.retry_after(email)}
            )

        user = await self._users.get_by_email(email)
        # Constant-time-ish authentication: when the user is missing, still run a
        # full Argon2 verification against a decoy hash so response timing does
        # not reveal whether the email exists (user-enumeration defence).
        if user is None:
            # ``_decoy_hash`` itself is an Argon2 hash on first use, so build it
            # on the worker thread too rather than on the event loop.
            decoy = await anyio.to_thread.run_sync(_decoy_hash)
            await self._hasher.verify_async(decoy, password)
            await self._throttle.record_failure(email)
            await self._audit.record(action="auth.login_failed", ip_address=ip)
            raise AuthenticationError("Invalid email or password.")
        if not await self._hasher.verify_async(user.password_hash, password):
            await self._throttle.record_failure(email)
            await self._audit.record(action="auth.login_failed", user_id=user.id, ip_address=ip)
            raise AuthenticationError("Invalid email or password.")
        if not user.is_active:
            raise AuthenticationError("This account is disabled.")

        if user.totp_enabled:
            if not totp_code:
                raise TwoFactorRequiredError()
            try:
                await self._consume_totp(user, totp_code)
            except AuthenticationError:
                await self._throttle.record_failure(email)
                await self._audit.record(action="auth.2fa_failed", user_id=user.id, ip_address=ip)
                raise

        # Success — clear the failure counter and any lock.
        await self._throttle.record_success(email)

        # Transparent password rehash if Argon2 parameters were upgraded.
        if self._hasher.needs_rehash(user.password_hash):
            user.password_hash = await self._hasher.hash_async(password)

        await self._audit.record(action="auth.login", user_id=user.id, ip_address=ip)
        return self._tokens.issue_pair(subject=user.id, role=user.role)

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Rotate a refresh token: verify, one-time-use invalidate, reissue."""
        claims = self._tokens.decode(refresh_token, expected_type="refresh")
        if await self._token_store.is_revoked(claims.jti):
            raise AuthenticationError("This refresh token has been revoked.")
        user = await self._users.get(claims.subject)
        if user is None or not user.is_active:
            raise AuthenticationError("User no longer active.")
        # Invalidate the presented refresh token so it cannot be replayed.
        await self._token_store.revoke(claims.jti, ttl_seconds=_remaining_ttl(claims.expires_at))
        return self._tokens.issue_pair(subject=user.id, role=user.role)

    async def logout(self, refresh_token: str) -> None:
        """Revoke a refresh token so the session cannot be renewed."""
        try:
            claims = self._tokens.decode(refresh_token, expected_type="refresh")
        except Exception:  # noqa: BLE001 - logout is best-effort and idempotent
            return
        await self._token_store.revoke(claims.jti, ttl_seconds=_remaining_ttl(claims.expires_at))
        await self._audit.record(action="auth.logout", user_id=claims.subject)

    async def get_user(self, user_id: str) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def begin_totp_enrollment(self, user_id: str) -> tuple[str, str]:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if user.totp_enabled:
            # Re-enrolling would overwrite the live secret using nothing but a
            # bearer token, so a stolen access token could swap the second
            # factor for the attacker's own device. Rotating requires disabling
            # first, which demands a valid code from the current authenticator.
            raise ConflictError(
                "Two-factor authentication is already enabled; disable it first to re-enrol."
            )
        secret = self._totp.generate_secret()
        # Store the (encrypted) secret immediately but keep 2FA disabled until
        # the user proves possession by verifying a code.
        user.totp_secret_enc = self._cipher.encrypt(secret)
        uri = self._totp.provisioning_uri(secret=secret, account_name=user.email)
        return secret, uri

    async def _consume_totp(self, user: User, code: str) -> None:
        """Validate a TOTP code and burn its time step so it cannot be replayed.

        Raises :class:`AuthenticationError` both for a wrong code and for a
        correct-but-already-used one; the two are deliberately indistinguishable
        to the caller.
        """
        secret = self._cipher.decrypt(user.totp_secret_enc or "")
        counter = self._totp.verify_counter(secret=secret, code=code)
        if counter is None:
            raise AuthenticationError("Invalid two-factor code.")
        if user.totp_last_counter is not None and counter <= user.totp_last_counter:
            log.warning("totp_code_replayed", user_id=user.id)
            raise AuthenticationError("Invalid two-factor code.")
        user.totp_last_counter = counter

    async def confirm_totp(self, *, user_id: str, code: str) -> None:
        user = await self._users.get(user_id)
        if user is None or not user.totp_secret_enc:
            raise NotFoundError("No pending TOTP enrolment.")
        await self._consume_totp(user, code)
        user.totp_enabled = True
        await self._audit.record(action="auth.2fa_enabled", user_id=user.id)

    async def disable_totp(self, *, user_id: str, code: str) -> None:
        """Disable 2FA, requiring a valid current code to prove possession."""
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if not user.totp_enabled or not user.totp_secret_enc:
            raise ConflictError("Two-factor authentication is not enabled.")
        await self._consume_totp(user, code)
        user.totp_enabled = False
        user.totp_secret_enc = None
        user.totp_last_counter = None
        await self._audit.record(action="auth.2fa_disabled", user_id=user.id)
