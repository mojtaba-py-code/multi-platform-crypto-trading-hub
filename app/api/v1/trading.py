"""Trading endpoints: place / cancel / list orders.

Order placement requires the ``order:create`` permission. Whether an order is
sent live or simulated is decided server-side by the exchange factory — clients
cannot force a live order.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUserDep, get_trading_service, require
from app.exchange.models import Order, Position
from app.schemas.common import Page, PageMeta
from app.schemas.trading import OrderCreate, OrderOut, PositionOut
from app.security.rbac import Permission
from app.services.trading_service import TradingService


def _exchange_order_to_out(order: Order) -> OrderOut:
    """Map a live/paper exchange Order DTO to the API OrderOut shape."""
    return OrderOut(
        id=order.id,
        exchange_order_id=order.id,
        symbol=order.symbol,
        side=order.side.value,
        type=order.type.value,
        status=order.status.value,
        amount=order.amount,
        filled=order.filled,
        price=order.price,
        average=order.average,
        fee=order.fee,
        is_paper=order.is_paper,
    )


def _position_to_out(p: Position) -> PositionOut:
    return PositionOut(
        symbol=p.symbol,
        side=p.side.value,
        contracts=p.contracts,
        entry_price=p.entry_price,
        mark_price=p.mark_price,
        leverage=p.leverage,
        unrealized_pnl=p.unrealized_pnl,
        liquidation_price=p.liquidation_price,
    )


router = APIRouter()

TradingDep = Annotated[TradingService, Depends(get_trading_service)]


@router.post(
    "/orders",
    response_model=OrderOut,
    dependencies=[Depends(require(Permission.order_create))],
)
async def place_order(payload: OrderCreate, user: CurrentUserDep, service: TradingDep) -> OrderOut:
    record = await service.place_order(
        user_id=user.id,
        account_id=payload.account_id,
        symbol=payload.symbol,
        side=payload.side,
        type_=payload.type,
        amount=payload.amount,
        price=payload.price,
        stop_price=payload.stop_price,
        time_in_force=payload.time_in_force,
        reduce_only=payload.reduce_only,
        post_only=payload.post_only,
        leverage=payload.leverage,
    )
    return OrderOut.model_validate(record)


@router.get(
    "/orders",
    response_model=Page[OrderOut],
    dependencies=[Depends(require(Permission.order_read))],
)
async def list_orders(
    user: CurrentUserDep,
    service: TradingDep,
    symbol: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[OrderOut]:
    items, total = await service.list_orders(
        user_id=user.id, symbol=symbol, status=status, page=page, page_size=page_size
    )
    return Page[OrderOut](
        items=[OrderOut.model_validate(i) for i in items],
        meta=PageMeta(page=page, page_size=page_size, total=total),
    )


@router.get(
    "/orders/{order_id}",
    response_model=OrderOut,
    dependencies=[Depends(require(Permission.order_read))],
)
async def get_order(order_id: str, user: CurrentUserDep, service: TradingDep) -> OrderOut:
    record = await service.get_order(user_id=user.id, order_id=order_id)
    return OrderOut.model_validate(record)


@router.delete(
    "/orders/{order_id}",
    response_model=OrderOut,
    dependencies=[Depends(require(Permission.order_cancel))],
    summary="Cancel an order by its id (updates the persisted record)",
)
async def cancel_order(order_id: str, user: CurrentUserDep, service: TradingDep) -> OrderOut:
    record = await service.cancel_order(user_id=user.id, order_id=order_id)
    return OrderOut.model_validate(record)


@router.get(
    "/accounts/{account_id}/open-orders",
    response_model=list[OrderOut],
    dependencies=[Depends(require(Permission.order_read))],
    summary="Live open orders reported by the exchange/paper adapter",
)
async def open_orders(account_id: str, user: CurrentUserDep, service: TradingDep) -> list[OrderOut]:
    orders = await service.list_open_orders(user_id=user.id, account_id=account_id)
    return [_exchange_order_to_out(o) for o in orders]


@router.get(
    "/accounts/{account_id}/positions",
    response_model=list[PositionOut],
    dependencies=[Depends(require(Permission.order_read))],
    summary="Open positions reported by the exchange (futures/margin)",
)
async def positions(
    account_id: str, user: CurrentUserDep, service: TradingDep
) -> list[PositionOut]:
    result = await service.list_positions(user_id=user.id, account_id=account_id)
    return [_position_to_out(p) for p in result]
