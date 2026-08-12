"""Strategy 2 — Order Block + Fair Value Gap continuation (SMC trend-following).

Trade *with* the institutional trend. In a clean higher-timeframe up-trend we wait
for a break of structure (proof buyers are in control), locate the last down-close
candle that fuelled that break — the bullish order block, often paired with a fair
value gap — and enter on the discounted retrace back into that zone.

    HTF up-trend + MTF bullish BOS + retrace into fresh OB/FVG  ->  long
    HTF down-trend + MTF bearish BOS + retrace into fresh OB/FVG -> short
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .. import indicators as ind
from .. import smc
from .. import structure as st
from ..signal import Signal, build_targets, grade_for, round_p
from .base import MarketData, Strategy


class OrderBlockFVG(Strategy):
    key = "obfvg"
    name = "Order Block + FVG Continuation"
    max_score = 10

    def analyze(self, md: MarketData) -> Optional[Signal]:
        if not self.ready(md):
            return None
        for side in ("LONG", "SHORT"):
            sig = self._analyze_side(md, side)
            if sig is not None:
                return sig
        return None

    def _analyze_side(self, md: MarketData, side: str) -> Optional[Signal]:
        long = side == "LONG"
        prec = md.precision
        price = md.price

        # --- 1. HTF trend must agree (required) ------------------------------
        htf_trend = st.trend(md.htf.high, md.htf.low)
        if long and htf_trend != "bullish":
            return None
        if not long and htf_trend != "bearish":
            return None

        # --- 2. recent MTF break of structure in trend direction (required) --
        mtf = md.mtf
        o, h, l, c = mtf.open, mtf.high, mtf.low, mtf.close
        breaks = st.structure_breaks(h, l, c)
        want_dir = "bullish" if long else "bearish"
        bos: Optional[st.Break] = None
        for b in breaks:
            if b.direction == want_dir and b.index >= len(mtf) - 40:
                bos = b
        if bos is None:
            return None

        # --- 3. the order block / FVG zone that fuelled the break (required) --
        ob = smc.order_block_before(o, h, l, c, bos.index, want_dir, lookback=20)
        fvgs = smc.find_fvgs(h, l, c)
        want_fvg = "bullish" if long else "bearish"
        fvg = smc.nearest_unmitigated_fvg(fvgs, want_fvg, price)

        zone: Optional[Tuple[float, float, bool, str]] = None
        if ob is not None:
            zone = (ob.low, ob.high, ob.mitigated, "order block")
        elif fvg is not None:
            zone = (fvg.low, fvg.high, fvg.mitigated, "fair value gap")
        if zone is None:
            return None
        zone_low, zone_high, mitigated, zone_name = zone
        if zone_high <= zone_low:
            return None
        range_z = zone_high - zone_low

        # --- 4. price must be retracing into / near the zone -----------------
        if long:
            in_reach = (zone_low - range_z * 1.0) <= price <= (zone_high + range_z * 0.5)
        else:
            in_reach = (zone_low - range_z * 0.5) <= price <= (zone_high + range_z * 1.0)
        if not in_reach:
            return None

        # --- 5. levels: entry zone, stop, targets ----------------------------
        entry_low, entry_high = zone_low, zone_high
        entry_ref = (zone_low + zone_high) / 2.0
        atr_m = ind.last(ind.atr(h, l, c, 14), default=range_z * 0.5)
        if atr_m > 0 and range_z > 2.5 * atr_m:
            return None  # order block too wide to be a precise entry
        buffer = max(0.3 * atr_m, range_z * 0.1)
        sl = (zone_low - buffer) if long else (zone_high + buffer)
        risk = abs(entry_ref - sl)
        if risk <= 0 or not (price > sl if long else price < sl):
            return None
        if entry_ref and (risk / entry_ref * 100.0) > self.max_risk_pct:
            return None  # too wide to be a sniper entry

        liq = self._target_liquidity(md, long)
        built = build_targets(side, entry_ref, sl, liq, prec)
        if built is None:
            return None
        tps, rrs = built

        # --- 6. scoring / confluences ----------------------------------------
        score = 6  # HTF trend (2) + MTF BOS (2) + zone present (2), all required
        confirmations: List[str] = [
            f"HTF {htf_trend} trend ({md.htf.interval})",
            f"{bos.kind} {want_dir} on {mtf.interval} (broke {round_p(bos.level, prec)})",
            f"{'Fresh' if not mitigated else 'Retested'} {zone_name} zone "
            f"{round_p(zone_low, prec)}-{round_p(zone_high, prec)}",
        ]

        # impulse leg (from zone to the BOS) for premium/discount + magnets
        seg_hi = min(bos.index + 5, len(mtf))
        seg_lo = ob.index if ob is not None else max(bos.index - 10, 0)
        leg_low = float(np.min(l[seg_lo:seg_hi]))
        leg_high = float(np.max(h[seg_lo:seg_hi]))
        leg_pd = smc.premium_discount(leg_low, leg_high, entry_ref)
        if (long and leg_pd == "discount") or (not long and leg_pd == "premium"):
            score += 1
            confirmations.append(f"Entry in {leg_pd} of the impulse")

        # OB + FVG confluence
        if ob is not None and fvg is not None and self._overlap(
            (zone_low, zone_high), (fvg.low, fvg.high)
        ):
            score += 1
            confirmations.append("Order block overlaps a fair value gap")

        # dynamic EMA confluence on the MTF
        ema50 = ind.last(ind.ema(c, 50))
        if np.isfinite(ema50) and abs(price - ema50) <= 0.6 * atr_m:
            score += 1
            confirmations.append("Confluence with 50-EMA dynamic S/R")

        # LTF reaction inside the zone
        if self._ltf_reaction(md, long):
            score += 1
            confirmations.append(f"Bullish reaction on {md.ltf.interval}" if long
                                 else f"Bearish reaction on {md.ltf.interval}")

        return Signal(
            strategy=self.name,
            strategy_key=self.key,
            symbol=md.symbol,
            side=side,
            score=min(score, self.max_score),
            max_score=self.max_score,
            grade=grade_for(min(score, self.max_score), self.max_score),
            entry_low=round_p(min(entry_low, entry_high), prec),
            entry_high=round_p(max(entry_low, entry_high), prec),
            entry_ref=round_p(entry_ref, prec),
            sl=round_p(sl, prec),
            tps=tps,
            rr=rrs,
            htf=f"{htf_trend} trend on {md.htf.interval}",
            mtf=f"{bos.kind} {want_dir} + {zone_name} on {mtf.interval}",
            ltf=f"Retrace entry, {md.ltf.interval} trigger",
            confirmations=confirmations,
            invalidation=(
                f"Close back {'below' if long else 'above'} {round_p(sl, prec)} "
                f"(order block broken)"
            ),
            price_precision=prec,
            atr=round_p(atr_m, prec),
            risk_pct=round(risk / entry_ref * 100.0, 2) if entry_ref else 0.0,
        )

    @staticmethod
    def _overlap(a: Tuple[float, float], b: Tuple[float, float]) -> bool:
        return max(a[0], b[0]) <= min(a[1], b[1])

    @staticmethod
    def _ltf_reaction(md: MarketData, long: bool) -> bool:
        ltf = md.ltf
        breaks = st.structure_breaks(ltf.high, ltf.low, ltf.close)
        want = "bullish" if long else "bearish"
        recent = len(ltf) - 8
        for b in breaks:
            if b.direction == want and b.index >= recent:
                return True
        # rejection candle on the last closed bar
        o, h, l, c = ltf.open[-1], ltf.high[-1], ltf.low[-1], ltf.close[-1]
        rng = h - l
        if rng <= 0:
            return False
        body = abs(c - o)
        if long and c > o and (min(o, c) - l) > body:
            return True
        if not long and c < o and (h - max(o, c)) > body:
            return True
        return False

    @staticmethod
    def _target_liquidity(md: MarketData, long: bool) -> List[float]:
        levels: List[float] = []
        for cd in (md.mtf, md.htf):
            highs, lows = smc.liquidity_levels(cd.high, cd.low)
            levels.extend(highs if long else lows)
        return levels
