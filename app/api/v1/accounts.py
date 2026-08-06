"""Exchange-account management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUserDep, get_account_service, require
from app.schemas.account import AccountCreate, AccountOut, PaperAccountCreate
from app.security.rbac import Permission
from app.services.account_service import AccountService

router = APIRouter()

AccountDep = Annotated[AccountService, Depends(get_account_service)]


@router.get(
    "",
    response_model=list[AccountOut],
    dependencies=[Depends(require(Permission.account_read))],
)
async def list_accounts(user: CurrentUserDep, service: AccountDep) -> list[AccountOut]:
    accounts = await service.list_accounts(user.id)
    return [AccountOut.model_validate(a) for a in accounts]


@router.get(
    "/{account_id}",
    response_model=AccountOut,
    dependencies=[Depends(require(Permission.account_read))],
)
async def get_account(account_id: str, user: CurrentUserDep, service: AccountDep) -> AccountOut:
    account = await service.get_account(account_id=account_id, user_id=user.id)
    return AccountOut.model_validate(account)


@router.post(
    "/paper",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.account_write))],
)
async def create_paper_account(
    payload: PaperAccountCreate, user: CurrentUserDep, service: AccountDep
) -> AccountOut:
    account = await service.create_paper_account(
        user_id=user.id,
        exchange_id=payload.exchange_id,
        label=payload.label,
        market_type=payload.market_type,
    )
    return AccountOut.model_validate(account)


@router.post(
    "/live",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.account_write))],
    summary="Connect a live exchange account (credentials are encrypted at rest)",
)
async def create_live_account(
    payload: AccountCreate, user: CurrentUserDep, service: AccountDep
) -> AccountOut:
    account = await service.create_live_account(
        user_id=user.id,
        exchange_id=payload.exchange_id,
        label=payload.label,
        market_type=payload.market_type,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        passphrase=payload.passphrase,
    )
    return AccountOut.model_validate(account)


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(Permission.account_write))],
)
async def delete_account(account_id: str, user: CurrentUserDep, service: AccountDep) -> None:
    await service.delete_account(account_id=account_id, user_id=user.id)
