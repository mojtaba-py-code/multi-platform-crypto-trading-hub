"""Additional indicator coverage: MACD, Stochastic, SuperTrend, ADX."""

from __future__ import annotations

from app.analytics import indicators
from app.analytics.models import Candle


def _wave_candles(n: int) -> list[Candle]:
    # A gentle oscillation so directional indicators have both up and down legs.
    import math

    out = []
    for i in range(n):
        price = 100 + 10 * math.sin(i / 5)
        out.append(
            Candle(
                timestamp=i,
                open=price,
                high=price + 2,
                low=price - 2,
                close=price,
                volume=5.0,
            )
        )
    return out


def test_macd_histogram_sign_flips_on_oscillation():
    closes = [c.close for c in _wave_candles(120)]
    _, _, hist = indicators.macd(closes)
    defined = [h for h in hist if h is not None]
    assert any(h > 0 for h in defined)
    assert any(h < 0 for h in defined)


def test_stochastic_bounded_0_100():
    candles = _wave_candles(60)
    k, d = indicators.stochastic(candles, 14, 3)
    for v in k:
        if v is not None:
            assert 0.0 <= v <= 100.0
    assert any(v is not None for v in d)


def test_supertrend_direction_values():
    candles = _wave_candles(80)
    line, direction = indicators.supertrend(candles, 10, 3.0)
    dirs = {d for d in direction if d is not None}
    assert dirs <= {1, -1}
    assert any(v is not None for v in line)


def test_adx_non_negative():
    candles = _wave_candles(100)
    result = indicators.adx(candles, 14)
    for v in result:
        if v is not None:
            assert v >= 0.0


def test_detect_trend_sideways():
    # Flat prices => neither up nor down.
    closes = [100.0] * 80
    assert indicators.detect_trend(closes) == "sideways"


def test_detect_trend_unknown_when_short():
    assert indicators.detect_trend([1, 2, 3], short=20, long=50) == "unknown"
