"""Smart-money concepts: fair value gaps, order blocks, liquidity pools,
liquidity sweeps and fibonacci / premium-discount helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from . import structure as st


@dataclass
class FVG:
    index: int      # middle candle of the 3-candle pattern
    type: str       # 'bullish' or 'bearish'
    low: float      # bottom of the gap
    high: float     # top of the gap
    mitigated: bool


@dataclass
class OrderBlock:
    index: int
    type: str       # 'bullish' or 'bearish'
    low: float
    high: float
    mitigated: bool


@dataclass
class Sweep:
    index: int
    side: str       # 'sellside' (grabbed lows) or 'buyside' (grabbed highs)
    level: float    # the liquidity level that was swept
    extreme: float  # wick extreme reached during the sweep


# ---------------------------------------------------------------- fair value gaps
def find_fvgs(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> List[FVG]:
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    out: List[FVG] = []
    n = high.size
    for i in range(1, n - 1):
        # bullish gap: candle i-1 high below candle i+1 low
        if low[i + 1] > high[i - 1]:
            gl, gh = float(high[i - 1]), float(low[i + 1])
            mitigated = bool(np.min(low[i + 2:]) <= gl) if i + 2 < n else False
            out.append(FVG(i, "bullish", gl, gh, mitigated))
        # bearish gap
        elif high[i + 1] < low[i - 1]:
            gl, gh = float(high[i + 1]), float(low[i - 1])
            mitigated = bool(np.max(high[i + 2:]) >= gh) if i + 2 < n else False
            out.append(FVG(i, "bearish", gl, gh, mitigated))
    return out


def nearest_unmitigated_fvg(fvgs: List[FVG], fvg_type: str, price: float) -> Optional[FVG]:
    candidates = [f for f in fvgs if f.type == fvg_type and not f.mitigated]
    if not candidates:
        return None
    # closest gap to current price
    return min(candidates, key=lambda f: abs(((f.low + f.high) / 2.0) - price))


# ---------------------------------------------------------------- order blocks
def order_block_before(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    break_index: int,
    direction: str,
    lookback: int = 15,
) -> Optional[OrderBlock]:
    """The last opposing candle before an impulse that caused a structure break.

    bullish break -> last down candle before it (bullish order block).
    bearish break -> last up candle before it (bearish order block).
    """
    start = max(break_index - 1, 0)
    stop = max(break_index - lookback, 0)
    n = close.size
    for i in range(start, stop - 1, -1):
        down = close[i] < open_[i]
        up = close[i] > open_[i]
        if direction == "bullish" and down:
            lo, hi = float(low[i]), float(high[i])
            mitigated = bool(np.min(low[i + 1:break_index + 1]) < lo) if i + 1 <= break_index else False
            return OrderBlock(i, "bullish", lo, hi, mitigated)
        if direction == "bearish" and up:
            lo, hi = float(low[i]), float(high[i])
            mitigated = bool(np.max(high[i + 1:break_index + 1]) > hi) if i + 1 <= break_index else False
            return OrderBlock(i, "bearish", lo, hi, mitigated)
    return None


# ---------------------------------------------------------------- liquidity
def liquidity_levels(high: np.ndarray, low: np.ndarray, left: int = 2, right: int = 2):
    """Return (buyside_levels, sellside_levels) = swing highs / swing lows prices."""
    highs = [float(high[i]) for i in st.swing_highs(high, left, right)]
    lows = [float(low[i]) for i in st.swing_lows(low, left, right)]
    return highs, lows


def detect_sweep(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    left: int = 2,
    right: int = 2,
    window: int = 6,
) -> Optional[Sweep]:
    """Look for a stop-hunt in the last `window` closed candles: price wicks beyond
    a prior swing level then closes back inside it."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = close.size
    if n < left + right + 3:
        return None

    sh = st.swing_highs(high, left, right)
    sl = st.swing_lows(low, left, right)

    start = max(n - window, right + 1)
    best: Optional[Sweep] = None
    for i in range(start, n):
        # sell-side sweep: took out a prior swing low but closed above it
        prior_lows = [k for k in sl if k < i - 1]
        if prior_lows:
            lvl = float(low[prior_lows[-1]])
            if low[i] < lvl and close[i] > lvl:
                best = Sweep(i, "sellside", lvl, float(low[i]))
        # buy-side sweep: took out a prior swing high but closed below it
        prior_highs = [k for k in sh if k < i - 1]
        if prior_highs:
            lvl = float(high[prior_highs[-1]])
            if high[i] > lvl and close[i] < lvl:
                # keep the most recent of either kind
                best = Sweep(i, "buyside", lvl, float(high[i]))
    return best


# ---------------------------------------------------------------- fibonacci
def fib_retracement(leg_low: float, leg_high: float, ratio: float) -> float:
    """Price at `ratio` retracement of an up-leg (0 == high, 1 == low)."""
    return leg_high - (leg_high - leg_low) * ratio


def ote_zone(leg_low: float, leg_high: float, side: str):
    """Optimal-trade-entry band (0.62-0.79 retracement)."""
    a = fib_retracement(leg_low, leg_high, 0.62)
    b = fib_retracement(leg_low, leg_high, 0.79)
    lo, hi = min(a, b), max(a, b)
    return lo, hi


def premium_discount(leg_low: float, leg_high: float, price: float) -> str:
    mid = (leg_low + leg_high) / 2.0
    if price < mid:
        return "discount"
    if price > mid:
        return "premium"
    return "equilibrium"
