"""Analytics endpoints: technical indicators and risk calculators.

Indicator endpoints hit the exchange for candles; the risk calculators are pure
math and require no network or credentials.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_market_service, require
from app.schemas.market import IndicatorOut
from app.schemas.trading import RiskCalcRequest, RiskCalcResult
from app.security.rbac import Permission
from app.services.market_service import MarketService
from app.trading import risk

router = APIRouter()

MarketDep = Annotated[MarketService, Depends(get_market_service)]


@router.get(
    "/{exchange_id}/indicator",
    response_model=IndicatorOut,
    # Fetches candles from the exchange — same abuse surface as /market.
    dependencies=[Depends(require(Permission.market_read))],
)
async def get_indicator(
    exchange_id: str,
    symbol: str,
    service: MarketDep,
    name: str = Query(examples=["rsi"]),
    timeframe: str = "1h",
    period: int = Query(default=14, ge=1, le=500),
    limit: int = Query(default=200, ge=10, le=1000),
) -> IndicatorOut:
    values = await service.indicator(
        exchange_id=exchange_id,
        symbol=symbol,
        name=name,
        timeframe=timeframe,
        period=period,
        limit=limit,
    )
    latest = next((v for v in reversed(values) if v is not None), None)
    return IndicatorOut(
        symbol=symbol, timeframe=timeframe, indicator=name.lower(), values=values, latest=latest
    )


@router.post("/risk/position-size", response_model=RiskCalcResult)
async def calc_position_size(payload: RiskCalcRequest) -> RiskCalcResult:
    sizing = risk.position_size(
        account_balance=payload.account_balance,
        risk_per_trade=payload.risk_per_trade,
        entry_price=payload.entry_price,
        stop_loss_price=payload.stop_loss_price,
        leverage=payload.leverage,
    )
    rr = None
    if payload.take_profit_price is not None:
        rr = risk.risk_reward_ratio(
            entry_price=payload.entry_price,
            stop_loss_price=payload.stop_loss_price,
            take_profit_price=payload.take_profit_price,
        )
    return RiskCalcResult(
        quantity=sizing.quantity,
        risk_amount=sizing.risk_amount,
        notional=sizing.notional,
        risk_reward_ratio=rr,
    )
