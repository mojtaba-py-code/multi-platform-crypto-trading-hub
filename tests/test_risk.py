"""Tests for the risk-management calculators."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.exceptions import RiskLimitError, ValidationError
from app.trading.enums import OrderSide
from app.trading.risk import (
    RiskLimits,
    liquidation_price,
    pnl,
    position_size,
    risk_reward_ratio,
)


def test_position_size_risk_bounded():
    sizing = position_size(
        account_balance=10_000,
        risk_per_trade=Decimal("0.01"),  # risk $100
        entry_price=100,
        stop_loss_price=95,
        leverage=10,
    )
    # Risking $100 with $5 per-unit risk => 20 units.
    assert sizing.quantity == pytest.approx(Decimal(20))
    assert sizing.risk_amount == pytest.approx(Decimal(100))
    assert sizing.notional == pytest.approx(Decimal(2000))


def test_position_size_capped_by_leverage():
    sizing = position_size(
        account_balance=1_000,
        risk_per_trade=Decimal("0.5"),
        entry_price=100,
        stop_loss_price=99,  # tiny stop -> huge raw size
        leverage=2,
    )
    # Notional cannot exceed balance * leverage = 2000 => qty <= 20.
    assert sizing.notional <= Decimal(2000)
    assert sizing.quantity <= Decimal(20)


def test_position_size_rejects_bad_input():
    with pytest.raises(ValidationError):
        position_size(
            account_balance=0, risk_per_trade=Decimal("0.01"), entry_price=1, stop_loss_price=2
        )
    with pytest.raises(ValidationError):
        position_size(
            account_balance=100, risk_per_trade=Decimal("0.01"), entry_price=1, stop_loss_price=1
        )


def test_pnl_long_and_short():
    assert pnl(side=OrderSide.buy, entry_price=100, exit_price=110, quantity=2) == Decimal(20)
    assert pnl(side=OrderSide.sell, entry_price=100, exit_price=90, quantity=2) == Decimal(20)


def test_pnl_with_fees():
    result = pnl(
        side=OrderSide.buy,
        entry_price=100,
        exit_price=110,
        quantity=1,
        fee_rate=Decimal("0.001"),
    )
    # gross 10 - fees (100+110)*0.001 = 10 - 0.21 = 9.79
    assert result == pytest.approx(Decimal("9.79"))


def test_liquidation_price_long_below_entry():
    lp = liquidation_price(side=OrderSide.buy, entry_price=100, leverage=10)
    assert lp < Decimal(100)


def test_liquidation_price_short_above_entry():
    lp = liquidation_price(side=OrderSide.sell, entry_price=100, leverage=10)
    assert lp > Decimal(100)


def test_risk_reward_ratio():
    rr = risk_reward_ratio(entry_price=100, stop_loss_price=95, take_profit_price=115)
    assert rr == pytest.approx(Decimal(3))  # reward 15 / risk 5


def test_risk_limits_reject_leverage():
    limits = RiskLimits(max_leverage=Decimal(20))
    with pytest.raises(RiskLimitError):
        limits.check_order(notional=100, equity=1000, leverage=50)


def test_risk_limits_reject_oversize():
    limits = RiskLimits(max_position_margin_pct=Decimal("0.5"))
    with pytest.raises(RiskLimitError):
        limits.check_order(notional=10_000, equity=1_000, leverage=1)


def test_risk_limits_accept_valid():
    limits = RiskLimits()
    # Should not raise.
    limits.check_order(notional=100, equity=1000, leverage=5, rr=Decimal(2))
