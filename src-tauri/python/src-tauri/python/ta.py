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
    n = len(a)
    result = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if not np.isnan(a[i]) and not np.isnan(b[i]) and not np.isnan(a[i-1]) and not np.isnan(b[i-1]):
            result[i] = a[i] > b[i] and a[i-1] <= b[i-1]
    return result


def crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """True when `a` crosses below `b`. Matches ta.crossunder."""
    n = len(a)
    result = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if not np.isnan(a[i]) and not np.isnan(b[i]) and not np.isnan(a[i-1]) and not np.isnan(b[i-1]):
            result[i] = a[i] < b[i] and a[i-1] >= b[i-1]
    return result


def mesa_mama_fama(
    source: np.ndarray,
    fast_limit: float,
    slow_limit: float
) -> Tuple[np.ndarray, np.ndarray]:
    """MESA Adaptive Moving Average (MAMA) and Following AMA (FAMA).

    Ehlers' MESA algorithm. Returns (mama, fama) arrays.
    Matches the f_mama_fama_crypto() function in Pine Script.

    Args:
        source: Price series (typically hl2).
        fast_limit: Fast alpha limit (e.g. 0.38).
        slow_limit: Slow alpha limit (e.g. 0.035).

    Returns:
        Tuple of (mama, fama) numpy arrays.
    """
    n = len(source)
    mama_arr = np.full(n, np.nan)
    fama_arr = np.full(n, np.nan)

    if n < 7:
        return mama_arr, fama_arr

    # Smooth = (4*src + 3*src[1] + 2*src[2] + src[3]) / 10
    smooth = np.full(n, np.nan)
    for i in range(3, n):
        smooth[i] = (4.0 * source[i] + 3.0 * source[i-1] +
                     2.0 * source[i-2] + source[i-3]) / 10.0

    # State variables (match Pine's `var` declarations)
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

        # Hilbert transform components
        s = smooth
        detrender[i] = (0.0962 * s[i] + 0.5769 * _gz(s, i-2) -
                        0.5769 * _gz(s, i-4) - 0.0962 * _gz(s, i-6)) * adj

        d = detrender
        Q1[i] = (0.0962 * d[i] + 0.5769 * _gz(d, i-2) -
                 0.5769 * _gz(d, i-4) - 0.0962 * _gz(d, i-6)) * adj
        I1[i] = d[i-3] if i >= 3 else 0.0

        i1 = I1
        q1 = Q1
        jI[i] = (0.0962 * i1[i] + 0.5769 * _gz(i1, i-2) -
                 0.5769 * _gz(i1, i-4) - 0.0962 * _gz(i1, i-6)) * adj
        jQ[i] = (0.0962 * q1[i] + 0.5769 * _gz(q1, i-2) -
                 0.5769 * _gz(q1, i-4) - 0.0962 * _gz(q1, i-6)) * adj

        I2[i] = I1[i] - jQ[i]
        Q2[i] = Q1[i] + jI[i]

        I2[i] = 0.2 * I2[i] + 0.8 * I2[i-1]
        Q2[i] = 0.2 * Q2[i] + 0.8 * Q2[i-1]

        Re[i] = I2[i] * I2[i-1] + Q2[i] * Q2[i-1]
        Im[i] = I2[i] * Q2[i-1] - Q2[i] * I2[i-1]

        Re[i] = 0.2 * Re[i] + 0.8 * Re[i-1]
        Im[i] = 0.2 * Im[i] + 0.8 * Im[i-1]

        # Period calculation
        p1 = period[i-1]
        if Im[i] != 0 and Re[i] != 0:
            p1 = 2.0 * np.pi / np.arctan(Im[i] / Re[i])

        # Pine: unconditional clamp against nz(period[1])
        # nz() returns 0.0 for na/0, so early bars get clamped to 0 then to 6
        prev_period = period[i-1]  # already 0.0 for early bars (matches nz)
        p1 = max(p1, 0.67 * prev_period)
        p1 = min(p1, 1.5 * prev_period)
        p1 = max(6.0, min(p1, 50.0))
        period[i] = 0.2 * p1 + 0.8 * period[i-1]

        # Phase
        if I1[i] != 0:
            phase[i] = np.arctan(Q1[i] / I1[i]) * 180.0 / np.pi
        else:
            phase[i] = phase[i-1]

        delta_phase = phase[i-1] - phase[i]
        if delta_phase < 1:
            delta_phase = 1

        alpha = fast_limit / delta_phase
        alpha = max(slow_limit, min(alpha, fast_limit))

        # MAMA and FAMA
        # Pine: var float mama = na → nz(mama[1]) returns 0.0 on first call
        prev_mama = mama_arr[i-1] if not np.isnan(mama_arr[i-1]) else 0.0
        prev_fama = fama_arr[i-1] if not np.isnan(fama_arr[i-1]) else 0.0

        mama_arr[i] = alpha * source[i] + (1 - alpha) * prev_mama
        fama_arr[i] = 0.5 * alpha * mama_arr[i] + (1 - 0.5 * alpha) * prev_fama

    return mama_arr, fama_arr


def _gz(arr: np.ndarray, idx: int) -> float:
    """Get value at index, return 0.0 if out of bounds or NaN (like nz)."""
    if idx < 0 or idx >= len(arr) or np.isnan(arr[idx]):
        return 0.0
    return arr[idx]
