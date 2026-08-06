"""Canonical trading enumerations shared across the exchange and API layers."""

from __future__ import annotations

from enum import StrEnum


class OrderSide(StrEnum):
    buy = "buy"
    sell = "sell"


class OrderType(StrEnum):
    market = "market"
    limit = "limit"
    stop = "stop"
    stop_limit = "stop_limit"
    take_profit = "take_profit"
    trailing_stop = "trailing_stop"


class TimeInForce(StrEnum):
    gtc = "GTC"  # good till cancelled
    ioc = "IOC"  # immediate or cancel
    fok = "FOK"  # fill or kill


class OrderStatus(StrEnum):
    pending = "pending"
    open = "open"
    partially_filled = "partially_filled"
    filled = "filled"
    canceled = "canceled"
    rejected = "rejected"
    expired = "expired"


class PositionSide(StrEnum):
    long = "long"
    short = "short"
    both = "both"  # one-way mode


class MarginMode(StrEnum):
    isolated = "isolated"
    cross = "cross"


class MarketType(StrEnum):
    spot = "spot"
    margin = "margin"
    futures = "futures"
    swap = "swap"
