"""Aggregate v1 API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import accounts, analytics, auth, market, portfolio, system, trading

api_router = APIRouter()
api_router.include_router(system.router, tags=["system"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api_router.include_router(market.router, prefix="/market", tags=["market"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(trading.router, prefix="/trading", tags=["trading"])
api_router.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
