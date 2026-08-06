"""Technical indicators implemented as pure functions.

Every function takes plain sequences of ``float`` and returns lists aligned to
the input length, using ``None`` for leading positions where the indicator is
not yet defined. Keeping these dependency-free (no numpy/pandas) makes them
trivial to unit-test and cheap to run inside request handlers.

The formulas follow the conventional definitions used by charting platforms
(Wilder's smoothing for RSI/ATR/ADX, etc.).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.analytics.models import Candle

Number = float
Series = Sequence[Number]
OptSeries = list[Number | None]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sma(values: Series, period: int) -> OptSeries:
    """Simple Moving Average."""
    _require(period > 0, "period must be positive")
    out: OptSeries = [None] * len(values)
    if len(values) < period:
        return out
    window_sum = sum(values[:period])
    out[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        out[i] = window_sum / period
    return out


def ema(values: Series, period: int) -> OptSeries:
    """Exponential Moving Average, seeded with the SMA of the first ``period``."""
    _require(period > 0, "period must be positive")
    out: OptSeries = [None] * len(values)
    if len(values) < period:
        return out
    multiplier = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (values[i] - prev) * multiplier + prev
        out[i] = prev
    return out


def wilder_smoothing(values: Series, period: int) -> OptSeries:
    """Wilder's smoothing (RMA), used by RSI, ATR and ADX."""
    _require(period > 0, "period must be positive")
    out: OptSeries = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rsi(closes: Series, period: int = 14) -> OptSeries:
    """Relative Strength Index using Wilder's smoothing."""
    _require(period > 0, "period must be positive")
    n = len(closes)
    out: OptSeries = [None] * n
    if n <= period:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)
    # First average over the initial ``period`` changes (indices 1..period).
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[OptSeries, OptSeries, OptSeries]:
    """MACD line, signal line and histogram."""
    _require(slow > fast > 0 and signal > 0, "require slow > fast > 0 and signal > 0")
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line: OptSeries = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema, strict=True)
    ]
    # Signal is an EMA of the defined portion of the MACD line.
    defined = [v for v in macd_line if v is not None]
    signal_defined = ema(defined, signal)
    signal_line: OptSeries = [None] * len(macd_line)
    hist: OptSeries = [None] * len(macd_line)
    offset = len(macd_line) - len(defined)
    for j, sig in enumerate(signal_defined):
        idx = offset + j
        signal_line[idx] = sig
        macd_val = macd_line[idx]
        if sig is not None and macd_val is not None:
            hist[idx] = macd_val - sig
    return macd_line, signal_line, hist


def bollinger_bands(
    closes: Series, period: int = 20, num_std: float = 2.0
) -> tuple[OptSeries, OptSeries, OptSeries]:
    """Bollinger Bands: (upper, middle, lower)."""
    _require(period > 0, "period must be positive")
    middle = sma(closes, period)
    upper: OptSeries = [None] * len(closes)
    lower: OptSeries = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = middle[i]
        assert mean is not None
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance**0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return upper, middle, lower


def true_range(candles: Sequence[Candle]) -> list[float]:
    """True Range series (first element uses high-low)."""
    tr: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            tr.append(c.high - c.low)
            continue
        prev_close = candles[i - 1].close
        tr.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
    return tr


def atr(candles: Sequence[Candle], period: int = 14) -> OptSeries:
    """Average True Range (Wilder)."""
    tr = true_range(candles)
    return wilder_smoothing(tr, period)


def vwap(candles: Sequence[Candle]) -> OptSeries:
    """Cumulative Volume Weighted Average Price."""
    out: OptSeries = []
    cum_pv = 0.0
    cum_vol = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3
        cum_pv += typical * c.volume
        cum_vol += c.volume
        out.append(cum_pv / cum_vol if cum_vol > 0 else None)
    return out


def stochastic(
    candles: Sequence[Candle], k_period: int = 14, d_period: int = 3
) -> tuple[OptSeries, OptSeries]:
    """Stochastic oscillator %K and %D."""
    n = len(candles)
    k: OptSeries = [None] * n
    for i in range(k_period - 1, n):
        window = candles[i - k_period + 1 : i + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        rng = highest - lowest
        k[i] = 100.0 * (candles[i].close - lowest) / rng if rng > 0 else 0.0
    k_defined = [v for v in k if v is not None]
    d_defined = sma(k_defined, d_period)
    d: OptSeries = [None] * n
    offset = n - len(k_defined)
    for j, val in enumerate(d_defined):
        d[offset + j] = val
    return k, d


def adx(candles: Sequence[Candle], period: int = 14) -> OptSeries:
    """Average Directional Index (Wilder)."""
    n = len(candles)
    out: OptSeries = [None] * n
    # The first ADX value appears at index 2*period-2, so it needs at least
    # 2*period-1 candles.
    if n < 2 * period - 1:
        return out
    tr = true_range(candles)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up_move = candles[i].high - candles[i - 1].high
        down_move = candles[i - 1].low - candles[i].low
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    atr_s = wilder_smoothing(tr, period)
    plus_s = wilder_smoothing(plus_dm, period)
    minus_s = wilder_smoothing(minus_dm, period)

    dx: OptSeries = [None] * n
    for i in range(n):
        a, p, m = atr_s[i], plus_s[i], minus_s[i]
        if a is None or p is None or m is None or a == 0:
            continue
        plus_di = 100.0 * p / a
        minus_di = 100.0 * m / a
        denom = plus_di + minus_di
        dx[i] = 100.0 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0

    dx_defined = [v for v in dx if v is not None]
    adx_defined = wilder_smoothing(dx_defined, period)
    offset = n - len(dx_defined)
    for j, val in enumerate(adx_defined):
        out[offset + j] = val
    return out


def supertrend(
    candles: Sequence[Candle], period: int = 10, multiplier: float = 3.0
) -> tuple[OptSeries, list[int | None]]:
    """SuperTrend line and direction (1 = uptrend, -1 = downtrend)."""
    n = len(candles)
    line: OptSeries = [None] * n
    direction: list[int | None] = [None] * n
    atr_s = atr(candles, period)
    prev_line: float | None = None
    prev_dir = 1
    for i in range(n):
        a = atr_s[i]
        if a is None:
            continue
        hl2 = (candles[i].high + candles[i].low) / 2
        upper = hl2 + multiplier * a
        lower = hl2 - multiplier * a
        close = candles[i].close
        if prev_line is None:
            prev_line = lower
            prev_dir = 1
        elif prev_dir == 1:
            lower = max(lower, prev_line)
            if close < lower:
                prev_dir = -1
                prev_line = upper
            else:
                prev_line = lower
        else:
            upper = min(upper, prev_line)
            if close > upper:
                prev_dir = 1
                prev_line = lower
            else:
                prev_line = upper
        line[i] = prev_line
        direction[i] = prev_dir
    return line, direction


def pivot_points(prev_high: float, prev_low: float, prev_close: float) -> dict[str, float]:
    """Classic (floor-trader) pivot points for the next period."""
    pivot = (prev_high + prev_low + prev_close) / 3
    return {
        "pivot": pivot,
        "r1": 2 * pivot - prev_low,
        "s1": 2 * pivot - prev_high,
        "r2": pivot + (prev_high - prev_low),
        "s2": pivot - (prev_high - prev_low),
        "r3": prev_high + 2 * (pivot - prev_low),
        "s3": prev_low - 2 * (prev_high - pivot),
    }


def detect_trend(closes: Series, short: int = 20, long: int = 50) -> str:
    """Very simple trend classification from two SMAs: up / down / sideways."""
    if len(closes) < long:
        return "unknown"
    s = sma(closes, short)[-1]
    long_ = sma(closes, long)[-1]
    if s is None or long_ is None:
        return "unknown"
    spread = (s - long_) / long_ if long_ else 0.0
    if spread > 0.001:
        return "up"
    if spread < -0.001:
        return "down"
    return "sideways"
