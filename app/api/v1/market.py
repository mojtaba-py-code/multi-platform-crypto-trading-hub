"""Market-data endpoints.

Every route here proxies an upstream exchange call, so all of them require an
authenticated caller with ``market:read``. Leaving them open would turn the
service into a free, unattributable proxy in front of the exchanges — and it is
*this* server's IP that gets rate limited or banned when someone abuses it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_market_service, require
from app.schemas.market import CandleOut, OrderBookLevelOut, OrderBookOut, TickerOut
from app.security.rbac import Permission
from app.services.market_service import MarketService

router = APIRouter(dependencies=[Depends(require(Permission.market_read))])

MarketDep = Annotated[MarketService, Depends(get_market_service)]


@router.get("/{exchange_id}/ticker", response_model=TickerOut)
async def get_ticker(exchange_id: str, symbol: str, service: MarketDep) -> TickerOut:
    t = await service.ticker(exchange_id, symbol)
    return TickerOut(
        symbol=t.symbol,
        last=t.last,
        bid=t.bid,
        ask=t.ask,
        high=t.high,
        low=t.low,
        base_volume=t.base_volume,
        timestamp=t.timestamp,
    )


@router.get("/{exchange_id}/orderbook", response_model=OrderBookOut)
async def get_order_book(
    exchange_id: str,
    symbol: str,
    service: MarketDep,
    limit: int = Query(default=50, ge=1, le=1000),
) -> OrderBookOut:
    ob = await service.order_book(exchange_id, symbol, limit)
    return OrderBookOut(
        symbol=ob.symbol,
        bids=[OrderBookLevelOut(price=lvl.price, amount=lvl.amount) for lvl in ob.bids],
        asks=[OrderBookLevelOut(price=lvl.price, amount=lvl.amount) for lvl in ob.asks],
        timestamp=ob.timestamp,
        spread=ob.spread,
    )


@router.get("/{exchange_id}/candles", response_model=list[CandleOut])
async def get_candles(
    exchange_id: str,
    symbol: str,
    service: MarketDep,
    timeframe: str = "1h",
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[CandleOut]:
    candles = await service.candles(exchange_id, symbol, timeframe, limit)
    return [
        CandleOut(
            timestamp=c.timestamp,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
        )
        for c in candles
    ]
