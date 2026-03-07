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
