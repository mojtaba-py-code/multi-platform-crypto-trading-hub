"""Tests for technical indicators, checked against hand-computed values."""

from __future__ import annotations

import pytest
from app.analytics import indicators
from app.analytics.models import Candle


def _candles(closes: list[float]) -> list[Candle]:
    return [
        Candle(timestamp=i, open=c, high=c + 1, low=c - 1, close=c, volume=10.0)
        for i, c in enumerate(closes)
    ]


def test_sma_basic():
    result = indicators.sma([1, 2, 3, 4, 5], 3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


def test_sma_insufficient_data():
    assert indicators.sma([1, 2], 5) == [None, None]


def test_ema_seeds_with_sma():
    values = [1, 2, 3, 4, 5, 6, 7, 8]
    result = indicators.ema(values, 3)
    assert result[2] == pytest.approx(2.0)  # seed = SMA of first 3
    # Next EMA: (4 - 2) * (2/4) + 2 = 3.0
    assert result[3] == pytest.approx(3.0)


def test_rsi_all_gains_is_100():
    closes = list(range(1, 20))  # strictly increasing
    result = indicators.rsi(closes, 14)
    assert result[-1] == pytest.approx(100.0)


def test_rsi_known_value():
    # Classic Wilder example sequence.
    closes = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.10,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
    ]
    result = indicators.rsi(closes, 14)
    assert result[14] == pytest.approx(70.46, abs=0.5)


def test_macd_shapes():
    closes = [float(x) for x in range(1, 60)]
    macd_line, signal, hist = indicators.macd(closes)
    assert len(macd_line) == len(signal) == len(hist) == len(closes)
    # In a linear uptrend the histogram should be finite where defined.
    assert any(h is not None for h in hist)


def test_bollinger_bands_ordering():
    closes = [10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
    upper, middle, lower = indicators.bollinger_bands(closes, 20, 2.0)
    assert upper[-1] > middle[-1] > lower[-1]


def test_atr_positive():
    candles = _candles([float(x) for x in range(1, 30)])
    result = indicators.atr(candles, 14)
    assert result[-1] is not None and result[-1] > 0


def test_vwap_monotone_reference():
    candles = _candles([10, 10, 10, 10])
    result = indicators.vwap(candles)
    assert all(v == pytest.approx(10.0) for v in result)


def test_pivot_points():
    p = indicators.pivot_points(prev_high=110, prev_low=90, prev_close=100)
    assert p["pivot"] == pytest.approx(100.0)
    assert p["r1"] > p["pivot"] > p["s1"]


def test_detect_trend_up():
    closes = [float(x) for x in range(1, 80)]
    assert indicators.detect_trend(closes) == "up"


def test_detect_trend_down():
    closes = [float(x) for x in range(80, 1, -1)]
    assert indicators.detect_trend(closes) == "down"


def test_invalid_period_raises():
    with pytest.raises(ValueError):
        indicators.sma([1, 2, 3], 0)
