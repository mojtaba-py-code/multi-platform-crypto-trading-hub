"""Application-wide exception hierarchy.

Every error the application raises on purpose derives from :class:`AppError`,
which carries an HTTP status code and a stable, machine-readable ``code``. The
API layer converts these into consistent JSON error responses, so business code
never has to know about HTTP details.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all expected application errors."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: dict | None = None) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict:
        payload: dict = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            payload["error"]["details"] = self.details
        return payload


# --- Authentication / authorization ---------------------------------------


class AuthenticationError(AppError):
    status_code = 401
    code = "authentication_error"
    message = "Authentication failed."


class InvalidTokenError(AuthenticationError):
    code = "invalid_token"
    message = "The provided token is invalid or expired."


class TwoFactorRequiredError(AuthenticationError):
    code = "two_factor_required"
    message = "A valid two-factor authentication code is required."


class AccountLockedError(AppError):
    status_code = 429
    code = "account_locked"
    message = "Too many failed attempts. This account is temporarily locked."


class PermissionDeniedError(AppError):
    status_code = 403
    code = "permission_denied"
    message = "You do not have permission to perform this action."


# --- Resource / validation -------------------------------------------------


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request payload failed validation."


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please slow down."


# --- Security --------------------------------------------------------------


class EncryptionError(AppError):
    code = "encryption_error"
    message = "Failed to encrypt or decrypt sensitive data."


# --- Exchange / trading ----------------------------------------------------


class ExchangeError(AppError):
    status_code = 502
    code = "exchange_error"
    message = "The upstream exchange returned an error."


class ExchangeUnavailableError(ExchangeError):
    status_code = 503
    code = "exchange_unavailable"
    message = "The exchange is currently unavailable."


class UnsupportedExchangeError(AppError):
    status_code = 400
    code = "unsupported_exchange"
    message = "The requested exchange is not supported."


class LiveTradingDisabledError(AppError):
    status_code = 403
    code = "live_trading_disabled"
    message = (
        "Live trading is disabled by server policy. Orders are simulated "
        "(paper trading) until an operator explicitly enables live trading."
    )


class InsufficientBalanceError(AppError):
    status_code = 400
    code = "insufficient_balance"
    message = "Insufficient balance for the requested operation."


class RiskLimitError(AppError):
    status_code = 400
    code = "risk_limit_exceeded"
    message = "The order violates a configured risk limit."


class CircuitOpenError(ExchangeUnavailableError):
    code = "circuit_open"
    message = "The circuit breaker is open for this exchange; try again shortly."
