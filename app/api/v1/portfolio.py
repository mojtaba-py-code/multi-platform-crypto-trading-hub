"""Portfolio endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, get_portfolio_service, require
from app.schemas.trading import BalanceOut
from app.security.rbac import Permission
from app.services.portfolio_service import PortfolioService

router = APIRouter()

PortfolioDep = Annotated[PortfolioService, Depends(get_portfolio_service)]


@router.get(
    "/balances",
    response_model=list[BalanceOut],
    dependencies=[Depends(require(Permission.portfolio_read))],
    summary="Aggregated balances across all connected accounts",
)
async def balances(user: CurrentUserDep, service: PortfolioDep) -> list[BalanceOut]:
    aggregated = await service.aggregate_balances(user.id)
    return [
        BalanceOut(asset=b.asset, free=b.free, used=b.used, total=b.total)
        for b in sorted(aggregated.values(), key=lambda b: b.asset)
    ]
