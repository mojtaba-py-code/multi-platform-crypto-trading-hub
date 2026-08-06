"""Trading, portfolio and risk-calculator schemas."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.trading.enums import OrderSide, OrderType, TimeInForce

# A market symbol like ``BTC/USDT`` or a derivatives symbol ``BTC/USDT:USDT``.
# Restricting the charset here is a defence-in-depth measure: the symbol is
# persisted and later rendered, so it must never carry HTML/script characters.
SYMBOL_PATTERN = r"^[A-Z0-9]{1,20}/[A-Z0-9]{1,20}(:[A-Z0-9]{1,20})?$"


class OrderCreate(BaseModel):
    account_id: str
    symbol: str = Field(examples=["BTC/USDT"], pattern=SYMBOL_PATTERN)
    side: OrderSide
    type: OrderType
    amount: Decimal = Field(gt=0)
    price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: TimeInForce = TimeInForce.gtc
    reduce_only: bool = False
    post_only: bool = False
    leverage: int | None = Field(default=None, ge=1, le=125)

    @model_validator(mode="after")
    def _price_required_for_limit(self) -> OrderCreate:
        if self.type in {OrderType.limit, OrderType.stop_limit} and self.price is None:
            raise ValueError(f"{self.type.value} orders require a price.")
        if self.type in {OrderType.stop, OrderType.stop_limit} and self.stop_price is None:
            raise ValueError(f"{self.type.value} orders require a stop_price.")
        return self


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    exchange_order_id: str | None = None
    symbol: str
    side: str
    type: str
    status: str
    amount: Decimal
    filled: Decimal
    price: Decimal | None
    average: Decimal | None
    fee: Decimal
    is_paper: bool


class BalanceOut(BaseModel):
    asset: str
    free: Decimal
    used: Decimal
    total: Decimal


class PositionOut(BaseModel):
    symbol: str
    side: str
    contracts: Decimal
    entry_price: Decimal
    mark_price: Decimal | None
    leverage: int
    unrealized_pnl: Decimal
    liquidation_price: Decimal | None


class RiskCalcRequest(BaseModel):
    account_balance: Decimal = Field(gt=0)
    risk_per_trade: Decimal = Field(gt=0, le=1, description="Fraction of account, e.g. 0.01 = 1%")
    entry_price: Decimal = Field(gt=0)
    stop_loss_price: Decimal = Field(gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    leverage: Decimal = Field(default=Decimal(1), gt=0, le=125)


class RiskCalcResult(BaseModel):
    quantity: Decimal
    risk_amount: Decimal
    notional: Decimal
    risk_reward_ratio: Decimal | None = None
