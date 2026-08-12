"""Hand-rolled technical indicators on NumPy arrays.

All functions return arrays the same length as the input, NaN-padded where a
value cannot yet be computed. No third-party TA dependency.
"""
from __future__ import annotations

import numpy as np


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder's running moving average (a.k.a. RMA / SMMA)."""
    x = np.asarray(x, dtype=np.float64)
    out = np.full(x.shape, np.nan)
    if x.size < n or n <= 0:
        return out
    out[n - 1] = x[:n].mean()
    alpha = 1.0 / n
    for i in range(n, x.size):
        out[i] = out[i - 1] + alpha * (x[i] - out[i - 1])
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    out = np.full(x.shape, np.nan)
    if x.size < n or n <= 0:
        return out
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    out = np.full(x.shape, np.nan)
    if x.size == 0 or n <= 0:
        return out
    k = 2.0 / (n + 1.0)
    out[0] = x[0]
    for i in range(1, x.size):
        out[i] = x[i] * k + out[i - 1] * (1.0 - k)
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    tr[0] = high[0] - low[0]
    return tr


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    return rma(true_range(high, low, close), n)


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    close = np.asarray(close, dtype=np.float64)
    out = np.full(close.shape, np.nan)
    if close.size < n + 1:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.clip(delta, 0.0, None)
    loss = np.clip(-delta, 0.0, None)
    ag = rma(gain, n)
    al = rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(al == 0.0, np.inf, ag / al)
        out = 100.0 - 100.0 / (1.0 + rs)
    return out


def last(x: np.ndarray, default: float = float("nan")) -> float:
    """Last finite value of an array (or default)."""
    x = np.asarray(x, dtype=np.float64)
    finite = x[np.isfinite(x)]
    return float(finite[-1]) if finite.size else default
