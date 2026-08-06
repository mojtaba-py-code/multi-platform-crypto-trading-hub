"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import (
    CurrentUserDep,
    client_ip,
    get_auth_service,
)
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenOut,
    TotpEnableOut,
    TotpVerifyRequest,
    UserOut,
)
from app.services.auth_service import AuthService

router = APIRouter()

AuthDep = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, service: AuthDep) -> UserOut:
    user = await service.register(email=payload.email, password=payload.password)
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginRequest, request: Request, service: AuthDep) -> TokenOut:
    pair = await service.authenticate(
        email=payload.email,
        password=payload.password,
        totp_code=payload.totp_code,
        ip=client_ip(request),
    )
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/refresh", response_model=TokenOut)
async def refresh(payload: RefreshRequest, service: AuthDep) -> TokenOut:
    pair = await service.refresh(payload.refresh_token)
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, service: AuthDep) -> None:
    await service.logout(payload.refresh_token)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUserDep, service: AuthDep) -> UserOut:
    current = await service.get_user(user.id)
    return UserOut.model_validate(current)


@router.post("/2fa/enroll", response_model=TotpEnableOut)
async def enroll_2fa(user: CurrentUserDep, service: AuthDep) -> TotpEnableOut:
    secret, uri = await service.begin_totp_enrollment(user.id)
    return TotpEnableOut(secret=secret, provisioning_uri=uri)


@router.post("/2fa/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_2fa(payload: TotpVerifyRequest, user: CurrentUserDep, service: AuthDep) -> None:
    await service.confirm_totp(user_id=user.id, code=payload.code)


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_2fa(payload: TotpVerifyRequest, user: CurrentUserDep, service: AuthDep) -> None:
    await service.disable_totp(user_id=user.id, code=payload.code)
