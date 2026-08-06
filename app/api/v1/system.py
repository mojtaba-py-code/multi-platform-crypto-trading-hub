"""System endpoints: health, readiness, and exchange registry."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_factory
from app.config import get_settings
from app.exchange.factory import ExchangeFactory
from app.exchange.registry import SUPPORTED_EXCHANGES

router = APIRouter()


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready(factory: Annotated[ExchangeFactory, Depends(get_factory)]) -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": str(settings.app_env),
        "live_trading_enabled": factory.live_trading_enabled,
        "testnet": settings.use_exchange_testnet,
    }


@router.get("/exchanges", summary="List supported exchanges")
async def list_exchanges() -> dict:
    return {
        "exchanges": [
            {
                "id": info.id,
                "name": info.name,
                "requires_passphrase": info.requires_passphrase,
                "supports_futures": info.supports_futures,
                "supports_margin": info.supports_margin,
                "has_testnet": info.has_testnet,
            }
            for info in SUPPORTED_EXCHANGES.values()
        ]
    }
