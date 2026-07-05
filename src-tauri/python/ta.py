"""Technical Analysis Library — vectorized numpy implementations.

All functions operate on numpy arrays and return numpy arrays.
Matches TradingView Pine Script ta.* functions.
"""

import numpy as np
from typing import Tuple


def nz(arr: np.ndarray, replacement: float = 0.0) -> np.ndarray:
    """Replace NaN values with replacement (like Pine's nz)."""
    result = arr.copy()
    result[np.isnan(result)] = replacement
    return result


def ema(source: np.ndarray, length: int) -> np.ndarray:
    """Exponential Moving Average matching TradingView's ta.ema."""
    n = len(source)
    result = np.full(n, np.nan)
    if n == 0 or length < 1:
        return result

    alpha = 2.0 / (length + 1)

    # Find first non-NaN value to seed
    start = 0
    while start < n and np.isnan(source[start]):
        start += 1
    if start >= n:
        return result

    # Seed with SMA of first `length` values
    if start + length <= n:
        result[start + length - 1] = np.nanmean(source[start:start + length])
        for i in range(start + length, n):
            result[i] = alpha * source[i] + (1 - alpha) * result[i - 1]
    else:
        result[start] = source[start]
        for i in range(start + 1, n):
            result[i] = alpha * source[i] + (1 - alpha) * result[i - 1]

    return result


def sma(source: np.ndarray, length: int) -> np.ndarray:
    """Simple Moving Average matching TradingView's ta.sma."""
    n = len(source)
    result = np.full(n, np.nan)
    if n < length or length < 1:
        return result

    cumsum = np.nancumsum(source)
    result[length - 1] = cumsum[length - 1] / length
    result[length:] = (cumsum[length:] - cumsum[:-length]) / length
    return result


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    """Average True Range matching TradingView's ta.atr (uses RMA/Wilder)."""
    n = len(close)
    tr = np.full(n, np.nan)

    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    # RMA (Wilder's smoothing) = EMA with alpha = 1/length
    return rma(tr, length)


def rma(source: np.ndarray, length: int) -> np.ndarray:
    """Wilder's Moving Average (RMA) matching TradingView's ta.rma."""
    n = len(source)
    result = np.full(n, np.nan)
    if n < length or length < 1:
        return result

    alpha = 1.0 / length
    result[length - 1] = np.nanmean(source[:length])
    for i in range(length, n):
        result[i] = alpha * source[i] + (1 - alpha) * result[i - 1]
    return result


def crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """True when `a` crosses above `b`. Matches ta.crossover."""
    result = np.zeros(len(a), dtype=bool)
    above = a > b
    below_or_eq = a[:-1] <= b[:-1]
    valid = ~np.isnan(a[1:]) & ~np.isnan(b[1:]) & ~np.isnan(a[:-1]) & ~np.isnan(b[:-1])
    result[1:] = above[1:] & below_or_eq & valid
    return result


def crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """True when `a` crosses below `b`. Matches ta.crossunder."""
    result = np.zeros(len(a), dtype=bool)
    below = a < b
    above_or_eq = a[:-1] >= b[:-1]
    valid = ~np.isnan(a[1:]) & ~np.isnan(b[1:]) & ~np.isnan(a[:-1]) & ~np.isnan(b[:-1])
    result[1:] = below[1:] & above_or_eq & valid
    return result


try:
    from numba import njit
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False


def _mesa_mama_fama_python(source, fast_limit, slow_limit):
    """Pure Python fallback for MESA."""
    n = len(source)
    mama_arr = np.full(n, np.nan)
    fama_arr = np.full(n, np.nan)

    if n < 7:
        return mama_arr, fama_arr

    smooth = np.full(n, np.nan)
    for i in range(3, n):
        smooth[i] = (4.0 * source[i] + 3.0 * source[i-1] +
                     2.0 * source[i-2] + source[i-3]) / 10.0

    detrender = np.zeros(n)
    I1 = np.zeros(n)
    Q1 = np.zeros(n)
    jI = np.zeros(n)
    jQ = np.zeros(n)
    I2 = np.zeros(n)
    Q2 = np.zeros(n)
    Re = np.zeros(n)
    Im = np.zeros(n)
    period = np.zeros(n)
    phase = np.zeros(n)

    for i in range(6, n):
        if np.isnan(smooth[i]):
            continue

        adj = 0.075 * period[i-1] + 0.54
        s = smooth

        def gz(arr, idx):
            if idx < 0 or idx >= n or np.isnan(arr[idx]):
                return 0.0
            return arr[idx]

        detrender[i] = (0.0962 * s[i] + 0.5769 * gz(s, i-2) -
                        0.5769 * gz(s, i-4) - 0.0962 * gz(s, i-6)) * adj

        d = detrender
        Q1[i] = (0.0962 * d[i] + 0.5769 * gz(d, i-2) -
                 0.5769 * gz(d, i-4) - 0.0962 * gz(d, i-6)) * adj
        I1[i] = d[i-3] if i >= 3 else 0.0

        i1 = I1
        q1 = Q1
        jI[i] = (0.0962 * i1[i] + 0.5769 * gz(i1, i-2) -
                 0.5769 * gz(i1, i-4) - 0.0962 * gz(i1, i-6)) * adj
        jQ[i] = (0.0962 * q1[i] + 0.5769 * gz(q1, i-2) -
                 0.5769 * gz(q1, i-4) - 0.0962 * gz(q1, i-6)) * adj

        I2[i] = I1[i] - jQ[i]
        Q2[i] = Q1[i] + jI[i]

        I2[i] = 0.2 * I2[i] + 0.8 * I2[i-1]
        Q2[i] = 0.2 * Q2[i] + 0.8 * Q2[i-1]

        Re[i] = I2[i] * I2[i-1] + Q2[i] * Q2[i-1]
        Im[i] = I2[i] * Q2[i-1] - Q2[i] * I2[i-1]

        Re[i] = 0.2 * Re[i] + 0.8 * Re[i-1]
        Im[i] = 0.2 * Im[i] + 0.8 * Im[i-1]

        p1 = period[i-1]
        if Im[i] != 0 and Re[i] != 0:
            p1 = 2.0 * np.pi / np.arctan(Im[i] / Re[i])

        prev_period = period[i-1]
        p1 = max(p1, 0.67 * prev_period)
        p1 = min(p1, 1.5 * prev_period)
        p1 = max(6.0, min(p1, 50.0))
        period[i] = 0.2 * p1 + 0.8 * period[i-1]

        if I1[i] != 0:
            phase[i] = np.arctan(Q1[i] / I1[i]) * 180.0 / np.pi
        else:
            phase[i] = phase[i-1]

        delta_phase = phase[i-1] - phase[i]
        if delta_phase < 1:
            delta_phase = 1

        alpha = fast_limit / delta_phase
        alpha = max(slow_limit, min(alpha, fast_limit))

        prev_mama = mama_arr[i-1] if not np.isnan(mama_arr[i-1]) else 0.0
        prev_fama = fama_arr[i-1] if not np.isnan(fama_arr[i-1]) else 0.0

        mama_arr[i] = alpha * source[i] + (1 - alpha) * prev_mama
        fama_arr[i] = 0.5 * alpha * mama_arr[i] + (1 - 0.5 * alpha) * prev_fama

    return mama_arr, fama_arr


if _HAS_NUMBA:
    @njit(cache=True)
    def _mesa_mama_fama_numba(source, fast_limit, slow_limit):
        """Numba-accelerated MESA MAMA/FAMA."""
        n = len(source)
        mama_arr = np.full(n, np.nan)
        fama_arr = np.full(n, np.nan)

        if n < 7:
            return mama_arr, fama_arr

        smooth = np.full(n, np.nan)
        for i in range(3, n):
            smooth[i] = (4.0 * source[i] + 3.0 * source[i-1] +
                         2.0 * source[i-2] + source[i-3]) / 10.0

        detrender = np.zeros(n)
        I1 = np.zeros(n)
        Q1 = np.zeros(n)
        jI = np.zeros(n)
        jQ = np.zeros(n)
        I2 = np.zeros(n)
        Q2 = np.zeros(n)
        Re = np.zeros(n)
        Im = np.zeros(n)
        period = np.zeros(n)
        phase = np.zeros(n)

        for i in range(6, n):
            si = smooth[i]
            if si != si:  # NaN check (numba-compatible)
                continue

            adj = 0.075 * period[i-1] + 0.54

            # Inline gz: safe array access returning 0.0 for out-of-bounds or NaN
            def gz(arr, idx):
                if idx < 0 or idx >= n:
                    return 0.0
                v = arr[idx]
                if v != v:  # NaN
                    return 0.0
                return v

            detrender[i] = (0.0962 * smooth[i] + 0.5769 * gz(smooth, i-2) -
                            0.5769 * gz(smooth, i-4) - 0.0962 * gz(smooth, i-6)) * adj

            Q1[i] = (0.0962 * detrender[i] + 0.5769 * gz(detrender, i-2) -
                     0.5769 * gz(detrender, i-4) - 0.0962 * gz(detrender, i-6)) * adj
            I1[i] = detrender[i-3] if i >= 3 else 0.0

            jI[i] = (0.0962 * I1[i] + 0.5769 * gz(I1, i-2) -
                     0.5769 * gz(I1, i-4) - 0.0962 * gz(I1, i-6)) * adj
            jQ[i] = (0.0962 * Q1[i] + 0.5769 * gz(Q1, i-2) -
                     0.5769 * gz(Q1, i-4) - 0.0962 * gz(Q1, i-6)) * adj

            I2[i] = I1[i] - jQ[i]
            Q2[i] = Q1[i] + jI[i]

            I2[i] = 0.2 * I2[i] + 0.8 * I2[i-1]
            Q2[i] = 0.2 * Q2[i] + 0.8 * Q2[i-1]

            Re[i] = I2[i] * I2[i-1] + Q2[i] * Q2[i-1]
            Im[i] = I2[i] * Q2[i-1] - Q2[i] * I2[i-1]

            Re[i] = 0.2 * Re[i] + 0.8 * Re[i-1]
            Im[i] = 0.2 * Im[i] + 0.8 * Im[i-1]

            p1 = period[i-1]
            if Im[i] != 0.0 and Re[i] != 0.0:
                p1 = 2.0 * 3.141592653589793 / np.arctan(Im[i] / Re[i])

            prev_period = period[i-1]
            if p1 < 0.67 * prev_period:
                p1 = 0.67 * prev_period
            if p1 > 1.5 * prev_period:
                p1 = 1.5 * prev_period
            if p1 < 6.0:
                p1 = 6.0
            if p1 > 50.0:
                p1 = 50.0
            period[i] = 0.2 * p1 + 0.8 * period[i-1]

            if I1[i] != 0.0:
                phase[i] = np.arctan(Q1[i] / I1[i]) * 57.29577951308232
            else:
                phase[i] = phase[i-1]

            delta_phase = phase[i-1] - phase[i]
            if delta_phase < 1.0:
                delta_phase = 1.0

            alpha = fast_limit / delta_phase
            if alpha < slow_limit:
                alpha = slow_limit
            if alpha > fast_limit:
                alpha = fast_limit

            prev_mama = mama_arr[i-1]
            if prev_mama != prev_mama:
                prev_mama = 0.0
            prev_fama = fama_arr[i-1]
            if prev_fama != prev_fama:
                prev_fama = 0.0

            mama_arr[i] = alpha * source[i] + (1.0 - alpha) * prev_mama
            fama_arr[i] = 0.5 * alpha * mama_arr[i] + (1.0 - 0.5 * alpha) * prev_fama

        return mama_arr, fama_arr


def mesa_mama_fama(
    source: np.ndarray,
    fast_limit: float,
    slow_limit: float
) -> Tuple[np.ndarray, np.ndarray]:
    """MESA Adaptive Moving Average (MAMA) and Following AMA (FAMA).

    Uses Numba JIT if available for ~50x speedup, falls back to pure Python.
    """
    if _HAS_NUMBA:
        return _mesa_mama_fama_numba(source, fast_limit, slow_limit)
    return _mesa_mama_fama_python(source, fast_limit, slow_limit)


def _gz(arr: np.ndarray, idx: int) -> float:
    """Get value at index, return 0.0 if out of bounds or NaN (like nz)."""
    if idx < 0 or idx >= len(arr) or np.isnan(arr[idx]):
        return 0.0
    return arr[idx]


# ═══════════════════════════════════════════════════════════════════════════
# Additional indicators — matches TradingView Pine Script ta.* functions
# ═══════════════════════════════════════════════════════════════════════════

def rsi(source: np.ndarray, length: int) -> np.ndarray:
    """Relative Strength Index matching TradingView's ta.rsi."""
    n = len(source)
    result = np.full(n, np.nan)
    if n < length + 1 or length < 1:
        return result

    delta = np.diff(source, prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)

    for i in range(n):
        if np.isnan(avg_gain[i]) or np.isnan(avg_loss[i]):
            continue
        if avg_loss[i] == 0:
            result[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            result[i] = 100.0 - (100.0 / (1.0 + rs))
    return result


def macd(source: np.ndarray, fast_length: int = 12, slow_length: int = 26,
         signal_length: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD matching TradingView's ta.macd. Returns (macd_line, signal_line, histogram)."""
    fast_ema = ema(source, fast_length)
    slow_ema = ema(source, slow_length)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal_length)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def stoch(close: np.ndarray, high: np.ndarray, low: np.ndarray,
          length: int) -> np.ndarray:
    """Stochastic %K matching TradingView's ta.stoch."""
    n = len(close)
    result = np.full(n, np.nan)
    if n < length:
        return result
    for i in range(length - 1, n):
        hh = np.max(high[i - length + 1:i + 1])
        ll = np.min(low[i - length + 1:i + 1])
        if hh != ll:
            result[i] = (close[i] - ll) / (hh - ll) * 100.0
        else:
            result[i] = 50.0
    return result


def bb(source: np.ndarray, length: int, mult: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands matching TradingView's ta.bb. Returns (middle, upper, lower)."""
    middle = sma(source, length)
    std = stdev(source, length)
    upper = middle + mult * std
    lower = middle - mult * std
    return middle, upper, lower


def bbw(source: np.ndarray, length: int, mult: float = 2.0) -> np.ndarray:
    """Bollinger Band Width matching TradingView's ta.bbw."""
    middle, upper, lower = bb(source, length, mult)
    result = np.full(len(source), np.nan)
    valid = ~np.isnan(middle) & (middle != 0)
    result[valid] = (upper[valid] - lower[valid]) / middle[valid]
    return result


def cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    """Commodity Channel Index matching TradingView's ta.cci."""
    tp = (high + low + close) / 3.0
    tp_sma = sma(tp, length)
    n = len(close)
    result = np.full(n, np.nan)
    for i in range(length - 1, n):
        mean_dev = np.mean(np.abs(tp[i - length + 1:i + 1] - tp_sma[i]))
        if mean_dev != 0:
            result[i] = (tp[i] - tp_sma[i]) / (0.015 * mean_dev)
        else:
            result[i] = 0.0
    return result


def wma(source: np.ndarray, length: int) -> np.ndarray:
    """Weighted Moving Average matching TradingView's ta.wma."""
    n = len(source)
    result = np.full(n, np.nan)
    if n < length or length < 1:
        return result
    weights = np.arange(1, length + 1, dtype=float)
    weight_sum = weights.sum()
    for i in range(length - 1, n):
        result[i] = np.sum(source[i - length + 1:i + 1] * weights) / weight_sum
    return result


def vwma(source: np.ndarray, volume: np.ndarray, length: int) -> np.ndarray:
    """Volume Weighted Moving Average matching TradingView's ta.vwma."""
    sv = source * volume
    return sma(sv, length) / sma(volume, length)


def hma(source: np.ndarray, length: int) -> np.ndarray:
    """Hull Moving Average matching TradingView's ta.hma."""
    import math
    half = max(1, length // 2)
    sqr = max(1, int(math.sqrt(length)))
    wma_half = wma(source, half)
    wma_full = wma(source, length)
    diff = 2.0 * wma_half - wma_full
    return wma(diff, sqr)


def dema(source: np.ndarray, length: int) -> np.ndarray:
    """Double Exponential Moving Average."""
    e1 = ema(source, length)
    e2 = ema(e1, length)
    return 2.0 * e1 - e2


def tema(source: np.ndarray, length: int) -> np.ndarray:
    """Triple Exponential Moving Average."""
    e1 = ema(source, length)
    e2 = ema(e1, length)
    e3 = ema(e2, length)
    return 3.0 * e1 - 3.0 * e2 + e3


def tr(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True Range matching TradingView's ta.tr."""
    n = len(close)
    result = np.full(n, np.nan)
    result[0] = high[0] - low[0]
    for i in range(1, n):
        result[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
    return result


def highest(source: np.ndarray, length: int) -> np.ndarray:
    """Highest value over last N bars matching TradingView's ta.highest."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(length - 1, n):
        result[i] = np.nanmax(source[i - length + 1:i + 1])
    return result


def lowest(source: np.ndarray, length: int) -> np.ndarray:
    """Lowest value over last N bars matching TradingView's ta.lowest."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(length - 1, n):
        result[i] = np.nanmin(source[i - length + 1:i + 1])
    return result


def highestbars(source: np.ndarray, length: int) -> np.ndarray:
    """Bars since highest value matching TradingView's ta.highestbars (returns negative offset)."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(length - 1, n):
        window = source[i - length + 1:i + 1]
        idx = np.nanargmax(window)
        result[i] = idx - (length - 1)  # negative offset
    return result


def lowestbars(source: np.ndarray, length: int) -> np.ndarray:
    """Bars since lowest value matching TradingView's ta.lowestbars (returns negative offset)."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(length - 1, n):
        window = source[i - length + 1:i + 1]
        idx = np.nanargmin(window)
        result[i] = idx - (length - 1)  # negative offset
    return result


def stdev(source: np.ndarray, length: int) -> np.ndarray:
    """Standard deviation matching TradingView's ta.stdev."""
    n = len(source)
    result = np.full(n, np.nan)
    if n < length or length < 1:
        return result
    for i in range(length - 1, n):
        result[i] = np.std(source[i - length + 1:i + 1], ddof=0)
    return result


def variance(source: np.ndarray, length: int) -> np.ndarray:
    """Variance matching TradingView's ta.variance."""
    sd = stdev(source, length)
    return sd ** 2


def change(source: np.ndarray, length: int = 1) -> np.ndarray:
    """Change matching TradingView's ta.change."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(length, n):
        result[i] = source[i] - source[i - length]
    return result


def mom(source: np.ndarray, length: int) -> np.ndarray:
    """Momentum matching TradingView's ta.mom (same as change)."""
    return change(source, length)


def roc(source: np.ndarray, length: int) -> np.ndarray:
    """Rate of Change matching TradingView's ta.roc."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(length, n):
        if source[i - length] != 0:
            result[i] = (source[i] - source[i - length]) / source[i - length] * 100.0
    return result


def mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        volume: np.ndarray, length: int) -> np.ndarray:
    """Money Flow Index matching TradingView's ta.mfi."""
    n = len(close)
    result = np.full(n, np.nan)
    tp = (high + low + close) / 3.0
    mf = tp * volume

    pos_mf = np.zeros(n)
    neg_mf = np.zeros(n)
    for i in range(1, n):
        if tp[i] > tp[i - 1]:
            pos_mf[i] = mf[i]
        elif tp[i] < tp[i - 1]:
            neg_mf[i] = mf[i]

    for i in range(length, n):
        pmf = np.sum(pos_mf[i - length + 1:i + 1])
        nmf = np.sum(neg_mf[i - length + 1:i + 1])
        if nmf == 0:
            result[i] = 100.0
        else:
            result[i] = 100.0 - (100.0 / (1.0 + pmf / nmf))
    return result


def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """On Balance Volume matching TradingView's ta.obv."""
    n = len(close)
    result = np.zeros(n)
    for i in range(1, n):
        if close[i] > close[i - 1]:
            result[i] = result[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            result[i] = result[i - 1] - volume[i]
        else:
            result[i] = result[i - 1]
    return result


def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         volume: np.ndarray, timestamps: np.ndarray = None,
         session_reset: bool = True) -> np.ndarray:
    """VWAP with optional session reset matching TradingView's ta.vwap.
    
    Args:
        high, low, close, volume: OHLCV arrays.
        timestamps: Unix timestamps (seconds or milliseconds). Required for session reset.
        session_reset: If True and timestamps provided, reset VWAP at each new trading day.
                      If False or no timestamps, compute cumulative VWAP from bar 0.
    
    TradingView's ta.vwap resets at the start of each session (new day for daily charts,
    new day in the instrument's exchange timezone for intraday charts).
    """
    n = len(close)
    tp = (high + low + close) / 3.0
    result = np.full(n, np.nan)
    
    if not session_reset or timestamps is None:
        # Cumulative VWAP (no reset)
        cum_tpv = np.cumsum(tp * volume)
        cum_vol = np.cumsum(volume)
        valid = cum_vol > 0
        result[valid] = cum_tpv[valid] / cum_vol[valid]
        return result
    
    # Session-reset VWAP: detect new days from timestamps
    from datetime import datetime, timezone, timedelta
    
    cum_tpv = 0.0
    cum_vol = 0.0
    prev_date = ""
    
    for i in range(n):
        ts = timestamps[i]
        if ts > 1e12:
            ts = ts / 1000  # ms to seconds
        
        # Convert to exchange timezone (CT for CME futures, UTC fallback)
        # Use CT (Central Time) — UTC-6 (CST) or UTC-5 (CDT)
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        year = dt_utc.year
        
        # DST check: 2nd Sunday of March to 1st Sunday of November
        mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
        mar_sun2 = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
        nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
        nov_sun1 = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
        is_dst = mar_sun2 <= dt_utc < nov_sun1
        ct_offset = timedelta(hours=-5 if is_dst else -6)
        dt_ct = dt_utc + ct_offset
        
        # Use CT date for session detection
        # CME Globex session starts at 17:00 CT (previous calendar day's evening)
        # So a bar at 17:00 CT Monday = start of Tuesday's session
        # Shift by +7 hours so 17:00 CT -> midnight = new "session day"
        session_dt = dt_ct + timedelta(hours=7)
        cur_date = session_dt.strftime("%Y-%m-%d")
        
        # Reset on new session day
        if cur_date != prev_date:
            cum_tpv = 0.0
            cum_vol = 0.0
            prev_date = cur_date
        
        cum_tpv += tp[i] * volume[i]
        cum_vol += volume[i]
        
        if cum_vol > 0:
            result[i] = cum_tpv / cum_vol
    
    return result


def linreg(source: np.ndarray, length: int, offset: int = 0) -> np.ndarray:
    """Linear Regression matching TradingView's ta.linreg."""
    n = len(source)
    result = np.full(n, np.nan)
    if n < length:
        return result
    x = np.arange(length, dtype=float)
    for i in range(length - 1, n):
        y = source[i - length + 1:i + 1]
        if np.any(np.isnan(y)):
            continue
        slope = (length * np.sum(x * y) - np.sum(x) * np.sum(y)) / \
                (length * np.sum(x * x) - np.sum(x) ** 2)
        intercept = (np.sum(y) - slope * np.sum(x)) / length
        result[i] = intercept + slope * (length - 1 - offset)
    return result


def cum(source: np.ndarray) -> np.ndarray:
    """Cumulative sum matching TradingView's ta.cum."""
    return np.nancumsum(source)


def rising(source: np.ndarray, length: int) -> np.ndarray:
    """True if source has been rising for length bars. Matches ta.rising."""
    n = len(source)
    result = np.zeros(n, dtype=bool)
    for i in range(length, n):
        result[i] = all(source[i - j] > source[i - j - 1] for j in range(length))
    return result


def falling(source: np.ndarray, length: int) -> np.ndarray:
    """True if source has been falling for length bars. Matches ta.falling."""
    n = len(source)
    result = np.zeros(n, dtype=bool)
    for i in range(length, n):
        result[i] = all(source[i - j] < source[i - j - 1] for j in range(length))
    return result


def barssince(condition: np.ndarray) -> np.ndarray:
    """Bars since condition was last true. Matches ta.barssince."""
    n = len(condition)
    result = np.full(n, np.nan)
    last_true = -1
    for i in range(n):
        if condition[i]:
            last_true = i
        if last_true >= 0:
            result[i] = i - last_true
    return result


def valuewhen(condition: np.ndarray, source: np.ndarray, occurrence: int = 0) -> np.ndarray:
    """Value of source when condition was last true. Matches ta.valuewhen."""
    n = len(source)
    result = np.full(n, np.nan)
    history = []
    for i in range(n):
        if condition[i]:
            history.append(source[i])
        if len(history) > occurrence:
            result[i] = history[-(occurrence + 1)]
    return result


def swma(source: np.ndarray) -> np.ndarray:
    """Symmetrically Weighted Moving Average (4-bar). Matches ta.swma."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(3, n):
        result[i] = (source[i - 3] + 2 * source[i - 2] + 2 * source[i - 1] + source[i]) / 6.0
    return result


def kc(high: np.ndarray, low: np.ndarray, close: np.ndarray,
       length: int, mult: float = 1.5, use_tr: bool = True) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Keltner Channel matching TradingView's ta.kc. Returns (middle, upper, lower)."""
    middle = ema(close, length)
    if use_tr:
        r = atr(high, low, close, length)
    else:
        r = rma(high - low, length)
    upper = middle + mult * r
    lower = middle - mult * r
    return middle, upper, lower


def donchian(high: np.ndarray, low: np.ndarray, length: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Donchian Channel. Returns (upper, lower, middle)."""
    upper = highest(high, length)
    lower = lowest(low, length)
    middle = (upper + lower) / 2.0
    return upper, lower, middle


def supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               length: int, factor: float) -> Tuple[np.ndarray, np.ndarray]:
    """Supertrend indicator. Returns (supertrend_line, direction)."""
    n = len(close)
    atr_val = atr(high, low, close, length)

    upper_band = np.full(n, np.nan)
    lower_band = np.full(n, np.nan)
    supertrend_arr = np.full(n, np.nan)
    direction = np.ones(n)  # 1 = up, -1 = down

    hl2 = (high + low) / 2.0

    for i in range(length, n):
        if np.isnan(atr_val[i]):
            continue
        basic_upper = hl2[i] + factor * atr_val[i]
        basic_lower = hl2[i] - factor * atr_val[i]

        if i == length or np.isnan(upper_band[i - 1]):
            upper_band[i] = basic_upper
            lower_band[i] = basic_lower
        else:
            upper_band[i] = basic_upper if basic_upper < upper_band[i - 1] or close[i - 1] > upper_band[i - 1] else upper_band[i - 1]
            lower_band[i] = basic_lower if basic_lower > lower_band[i - 1] or close[i - 1] < lower_band[i - 1] else lower_band[i - 1]

        if i == length or np.isnan(supertrend_arr[i - 1]):
            supertrend_arr[i] = upper_band[i]
            direction[i] = -1
        elif supertrend_arr[i - 1] == upper_band[i - 1]:
            if close[i] > upper_band[i]:
                supertrend_arr[i] = lower_band[i]
                direction[i] = 1
            else:
                supertrend_arr[i] = upper_band[i]
                direction[i] = -1
        else:
            if close[i] < lower_band[i]:
                supertrend_arr[i] = upper_band[i]
                direction[i] = -1
            else:
                supertrend_arr[i] = lower_band[i]
                direction[i] = 1

    return supertrend_arr, direction

