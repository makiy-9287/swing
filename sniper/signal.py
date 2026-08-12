"""The Signal data model plus shared helpers for grading and target building."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Signal:
    strategy: str          # human readable strategy name
    strategy_key: str      # 'sweep' | 'obfvg'
    symbol: str
    side: str              # 'LONG' | 'SHORT'
    score: int
    max_score: int
    grade: str             # 'A' | 'B' | 'C'

    entry_low: float
    entry_high: float
    entry_ref: float
    sl: float
    tps: List[float]
    rr: List[float]

    htf: str               # HTF bias narrative
    mtf: str               # MTF structure narrative
    ltf: str               # LTF trigger narrative
    confirmations: List[str]
    invalidation: str

    price_precision: int
    atr: float
    risk_pct: float
    created_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.symbol}:{self.strategy_key}:{self.side}"


# ------------------------------------------------------------------ helpers
def grade_for(score: int, max_score: int) -> str:
    ratio = score / max_score if max_score else 0.0
    if ratio >= 0.8:
        return "A"
    if ratio >= 0.6:
        return "B"
    return "C"


def round_p(value: float, precision: int) -> float:
    return round(float(value), precision)


def build_targets(
    side: str,
    entry_ref: float,
    sl: float,
    liquidity: List[float],
    precision: int,
    r_floors: Tuple[float, float, float] = (1.0, 2.0, 3.0),
    snap_cap: float = 1.8,
) -> Optional[Tuple[List[float], List[float]]]:
    """Build a clean three-rung take-profit ladder.

    Each rung has a minimum R:R floor (1R / 2R / 3R). For each floor we look for a
    real liquidity pool (swing high/low where stops rest) at or beyond that floor
    and snap the target to it — institutions target liquidity, not round numbers —
    but never reach past ``snap_cap`` x the floor to keep targets realistic. If no
    such pool exists we use the R-multiple itself. Result is always monotonic and
    strictly beyond the entry.
    """
    long = side == "LONG"
    risk = abs(entry_ref - sl)
    if risk <= 0:
        return None

    if long:
        pool = sorted(l for l in liquidity if l > entry_ref)
    else:
        pool = sorted((l for l in liquidity if l < entry_ref), reverse=True)

    tps: List[float] = []
    rrs: List[float] = []
    prev: Optional[float] = None

    for floor in r_floors:
        ideal = entry_ref + (risk * floor if long else -risk * floor)
        chosen = ideal
        for lvl in pool:
            beyond_ideal = lvl >= ideal if long else lvl <= ideal
            beyond_prev = True if prev is None else (lvl > prev if long else lvl < prev)
            if not (beyond_ideal and beyond_prev):
                continue
            rr = abs(lvl - entry_ref) / risk
            if rr <= floor * snap_cap:  # snap to this real pool
                chosen = lvl
            break  # first pool at/beyond the floor decides this rung

        # enforce strictly-progressing rungs (>= 0.3R beyond the previous target)
        if prev is not None:
            min_next = prev + risk * 0.3 if long else prev - risk * 0.3
            if long:
                chosen = max(chosen, min_next)
            else:
                chosen = min(chosen, min_next)
        prev = chosen
        tps.append(round_p(chosen, precision))
        rrs.append(round(abs(chosen - entry_ref) / risk, 2))

    return tps, rrs
