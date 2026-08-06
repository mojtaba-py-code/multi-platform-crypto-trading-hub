"""FastAPI dependency-injection wiring.

Constructs repositories and services per request from the request-scoped
database session. Keeping construction here (rather than global singletons)
means each request gets a clean unit of work, and tests can override any piece.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError
from app.database.session import get_session
from app.exchange.factory import ExchangeFactory, get_exchange_factory
from app.repositories.account_repository import AccountRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.user_repository import UserRepository
from app.security.crypto import (
    PasswordHasher,
    SecretCipher,
    get_password_hasher,
    get_secret_cipher,
)
from app.security.rbac import Permission, has_permission
from app.security.throttle import LoginThrottle, get_login_throttle
from app.security.token_store import TokenStore, get_token_store
from app.security.tokens import TokenClaims, TokenService, get_token_service
from app.security.totp import TotpService
from app.services.account_service import AccountService
from app.services.auth_service import AuthService
from app.services.market_service import MarketService
from app.services.portfolio_service import PortfolioService
from app.services.trading_service import TradingService

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=True)


def get_cipher() -> SecretCipher:
    return get_secret_cipher()


def get_hasher() -> PasswordHasher:
    return get_password_hasher()


def get_tokens() -> TokenService:
    return get_token_service()


def get_totp() -> TotpService:
    return TotpService()


def get_tokstore() -> TokenStore:
    return get_token_store()


def get_throttle() -> LoginThrottle:
    return get_login_throttle()


def get_factory() -> ExchangeFactory:
    return get_exchange_factory()


# --- Service factories -----------------------------------------------------


def get_auth_service(
    session: SessionDep,
    cipher: Annotated[SecretCipher, Depends(get_cipher)],
    hasher: Annotated[PasswordHasher, Depends(get_hasher)],
    tokens: Annotated[TokenService, Depends(get_tokens)],
    totp: Annotated[TotpService, Depends(get_totp)],
    token_store: Annotated[TokenStore, Depends(get_tokstore)],
    throttle: Annotated[LoginThrottle, Depends(get_throttle)],
) -> AuthService:
    return AuthService(
        users=UserRepository(session),
        audit=AuditRepository(session),
        hasher=hasher,
        tokens=tokens,
        totp=totp,
        cipher=cipher,
        token_store=token_store,
        throttle=throttle,
    )


def get_account_service(
    session: SessionDep,
    cipher: Annotated[SecretCipher, Depends(get_cipher)],
    factory: Annotated[ExchangeFactory, Depends(get_factory)],
) -> AccountService:
    return AccountService(
        accounts=AccountRepository(session),
        audit=AuditRepository(session),
        cipher=cipher,
        factory=factory,
    )


def get_market_service(
    factory: Annotated[ExchangeFactory, Depends(get_factory)],
) -> MarketService:
    return MarketService(factory)


def get_trading_service(
    session: SessionDep,
    account_service: Annotated[AccountService, Depends(get_account_service)],
) -> TradingService:
    return TradingService(
        accounts=account_service,
        orders=OrderRepository(session),
        audit=AuditRepository(session),
    )


def get_portfolio_service(
    account_service: Annotated[AccountService, Depends(get_account_service)],
) -> PortfolioService:
    return PortfolioService(account_service)


# --- Authentication --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: str
    role: str


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    tokens: Annotated[TokenService, Depends(get_tokens)],
) -> CurrentUser:
    claims: TokenClaims = tokens.decode(credentials.credentials, expected_type="access")
    return CurrentUser(id=claims.subject, role=claims.role)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require(permission: Permission):
    """Return a dependency that enforces a single RBAC permission."""

    async def _dep(user: CurrentUserDep) -> CurrentUser:
        if not has_permission(user.role, permission):
            raise PermissionDeniedError(
                f"Role '{user.role}' lacks permission '{permission.value}'."
            )
        return user

    return _dep


def client_ip(request: Request) -> str | None:
    if request.client:
        return request.client.host
    return None
