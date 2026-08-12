"""Market-structure primitives: swing points, trend classification and
BOS / CHoCH (break of structure / change of character) detection.

These are the backbone of "smart money" reading — they tell us whether price is
respecting an up-trend, a down-trend, or has just shifted character.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class Pivot:
    index: int
    price: float
    kind: str  # 'H' or 'L'


@dataclass
class Break:
    index: int          # candle that closed through the level
    kind: str           # 'BOS' or 'CHoCH'
    direction: str      # 'bullish' or 'bearish'
    level: float        # the swing level that was broken


def swing_highs(high: np.ndarray, left: int = 2, right: int = 2) -> List[int]:
    """Confirmed fractal swing-high indices (strictly higher than neighbours)."""
    high = np.asarray(high, dtype=np.float64)
    n = high.size
    out: List[int] = []
    for i in range(left, n - right):
        pivot = high[i]
        if pivot > high[i - left:i].max() and pivot > high[i + 1:i + right + 1].max():
            out.append(i)
    return out


def swing_lows(low: np.ndarray, left: int = 2, right: int = 2) -> List[int]:
    low = np.asarray(low, dtype=np.float64)
    n = low.size
    out: List[int] = []
    for i in range(left, n - right):
        pivot = low[i]
        if pivot < low[i - left:i].min() and pivot < low[i + 1:i + right + 1].min():
            out.append(i)
    return out


def pivots(high: np.ndarray, low: np.ndarray, left: int = 2, right: int = 2) -> List[Pivot]:
    ph = [Pivot(i, float(high[i]), "H") for i in swing_highs(high, left, right)]
    pl = [Pivot(i, float(low[i]), "L") for i in swing_lows(low, left, right)]
    return sorted(ph + pl, key=lambda p: p.index)


def trend(high: np.ndarray, low: np.ndarray, left: int = 2, right: int = 2) -> str:
    """Classic HH/HL vs LH/LL classification on the last two pivots of each type."""
    sh = swing_highs(high, left, right)
    sl = swing_lows(low, left, right)
    if len(sh) >= 2 and len(sl) >= 2:
        hh = high[sh[-1]] > high[sh[-2]]
        hl = low[sl[-1]] > low[sl[-2]]
        lh = high[sh[-1]] < high[sh[-2]]
        ll = low[sl[-1]] < low[sl[-2]]
        if hh and hl:
            return "bullish"
        if lh and ll:
            return "bearish"
    return "range"


def structure_breaks(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    left: int = 2,
    right: int = 2,
) -> List[Break]:
    """Walk the series maintaining the most recent *confirmed* swing high/low as
    protected levels. A close beyond one is a structure break: BOS if it agrees
    with the running trend, CHoCH if it flips it."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = close.size

    sh = set(swing_highs(high, left, right))
    sl = set(swing_lows(low, left, right))

    resistance: Optional[float] = None
    support: Optional[float] = None
    state = "range"
    breaks: List[Break] = []

    for i in range(n):
        j = i - right  # a pivot at j only becomes *confirmed* right bars later
        if j >= 0:
            if j in sh:
                resistance = float(high[j])
            if j in sl:
                support = float(low[j])

        c = close[i]
        if resistance is not None and c > resistance:
            kind = "BOS" if state == "bullish" else "CHoCH"
            breaks.append(Break(i, kind, "bullish", resistance))
            state = "bullish"
            resistance = None
        elif support is not None and c < support:
            kind = "BOS" if state == "bearish" else "CHoCH"
            breaks.append(Break(i, kind, "bearish", support))
            state = "bearish"
            support = None

    return breaks


def last_break(breaks: List[Break], direction: Optional[str] = None,
               kind: Optional[str] = None) -> Optional[Break]:
    for b in reversed(breaks):
        if direction and b.direction != direction:
            continue
        if kind and b.kind != kind:
            continue
        return b
    return None
