"""Risk-management calculators.

Pure, side-effect-free financial math used both by the API (calculator
endpoints) and by the order engine's pre-trade risk checks. Everything is
expressed with :class:`~decimal.Decimal` internally to avoid float drift on
money, then returned as ``Decimal`` for the caller to quantise.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.exceptions import RiskLimitError, ValidationError
from app.trading.enums import OrderSide


def _d(value: float | int | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True, slots=True)
class PositionSizing:
    quantity: Decimal
    risk_amount: Decimal
    notional: Decimal


def position_size(
    *,
    account_balance: float | Decimal,
    risk_per_trade: float | Decimal,
    entry_price: float | Decimal,
    stop_loss_price: float | Decimal,
    leverage: float | Decimal = 1,
) -> PositionSizing:
    """Size a position so that hitting the stop loses exactly ``risk_per_trade``.

    ``risk_per_trade`` is a fraction of the account (e.g. ``0.01`` for 1%).
    Returns the base-asset quantity, the currency amount at risk, and the
    position notional.
    """
    balance = _d(account_balance)
    risk_fraction = _d(risk_per_trade)
    entry = _d(entry_price)
    stop = _d(stop_loss_price)
    lev = _d(leverage)

    if balance <= 0:
        raise ValidationError("account_balance must be positive.")
    if not (0 < risk_fraction <= 1):
        raise ValidationError("risk_per_trade must be in (0, 1].")
    if entry <= 0 or stop <= 0:
        raise ValidationError("prices must be positive.")
    if entry == stop:
        raise ValidationError("entry_price and stop_loss_price must differ.")
    if lev <= 0:
        raise ValidationError("leverage must be positive.")

    risk_amount = balance * risk_fraction
    per_unit_risk = abs(entry - stop)
    quantity = risk_amount / per_unit_risk
    notional = quantity * entry

    # Cap by available buying power so we never exceed the account's leverage.
    max_notional = balance * lev
    if notional > max_notional:
        quantity = max_notional / entry
        notional = quantity * entry
    return PositionSizing(quantity=quantity, risk_amount=risk_amount, notional=notional)


def pnl(
    *,
    side: OrderSide,
    entry_price: float | Decimal,
    exit_price: float | Decimal,
    quantity: float | Decimal,
    fee_rate: float | Decimal = 0,
) -> Decimal:
    """Realised profit/loss for a round-trip trade, net of taker fees on both legs."""
    entry = _d(entry_price)
    exit_ = _d(exit_price)
    qty = _d(quantity)
    fee = _d(fee_rate)
    if qty <= 0 or entry <= 0 or exit_ <= 0:
        raise ValidationError("prices and quantity must be positive.")
    gross = (exit_ - entry) * qty if side is OrderSide.buy else (entry - exit_) * qty
    fees = (entry + exit_) * qty * fee
    return gross - fees


def liquidation_price(
    *,
    side: OrderSide,
    entry_price: float | Decimal,
    leverage: float | Decimal,
    maintenance_margin_rate: float | Decimal = Decimal("0.005"),
) -> Decimal:
    """Approximate isolated-margin liquidation price.

    A simplified model (ignores funding and fees) suitable for pre-trade risk
    display, not for exchange-exact liquidation. For a long, price may fall by
    roughly ``1/leverage`` before the margin is exhausted.
    """
    entry = _d(entry_price)
    lev = _d(leverage)
    mmr = _d(maintenance_margin_rate)
    if lev <= 0 or entry <= 0:
        raise ValidationError("entry_price and leverage must be positive.")
    imr = Decimal(1) / lev  # initial margin rate
    if side is OrderSide.buy:
        return entry * (Decimal(1) - imr + mmr)
    return entry * (Decimal(1) + imr - mmr)


def risk_reward_ratio(
    *,
    entry_price: float | Decimal,
    stop_loss_price: float | Decimal,
    take_profit_price: float | Decimal,
) -> Decimal:
    """Reward-to-risk ratio (reward divided by risk)."""
    entry = _d(entry_price)
    stop = _d(stop_loss_price)
    target = _d(take_profit_price)
    risk = abs(entry - stop)
    if risk == 0:
        raise ValidationError("stop_loss_price must differ from entry_price.")
    reward = abs(target - entry)
    return reward / risk


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Portfolio-level guard rails checked before an order is accepted."""

    max_leverage: Decimal = Decimal(20)
    # Caps the initial *margin* committed to a single position (notional /
    # leverage) at this fraction of account equity. Equivalently:
    # notional ≤ equity × leverage × max_position_margin_pct. This keeps the
    # rule meaningful under leverage instead of forbidding it.
    max_position_margin_pct: Decimal = Decimal("0.5")
    min_risk_reward: Decimal = Decimal(1)

    def check_order(
        self,
        *,
        notional: float | Decimal,
        equity: float | Decimal,
        leverage: float | Decimal,
        rr: float | Decimal | None = None,
    ) -> None:
        """Raise :class:`RiskLimitError` if the order breaches any limit."""
        notional_d = _d(notional)
        equity_d = _d(equity)
        lev = _d(leverage)
        if equity_d <= 0:
            raise RiskLimitError("Account equity must be positive to trade.")
        if lev > self.max_leverage:
            raise RiskLimitError(
                f"Leverage {lev} exceeds the maximum of {self.max_leverage}.",
                details={"limit": "max_leverage"},
            )
        if notional_d > equity_d * lev * self.max_position_margin_pct:
            raise RiskLimitError(
                "Position margin exceeds the maximum share of account equity.",
                details={"limit": "max_position_margin_pct"},
            )
        if rr is not None and _d(rr) < self.min_risk_reward:
            raise RiskLimitError(
                f"Reward-to-risk {rr} is below the minimum of {self.min_risk_reward}.",
                details={"limit": "min_risk_reward"},
            )
