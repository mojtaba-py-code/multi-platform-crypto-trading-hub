"""Shared value objects for market analysis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candle:
    """A single OHLCV candlestick.

    ``timestamp`` is milliseconds since the Unix epoch (the ccxt convention).
    """

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_ccxt(cls, row: list[float]) -> Candle:
        """Build a candle from a ccxt OHLCV row ``[ts, o, h, l, c, v]``."""
        ts, o, h, low, c, v = row[:6]
        return cls(int(ts), float(o), float(h), float(low), float(c), float(v))
