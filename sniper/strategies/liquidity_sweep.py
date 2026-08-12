"""Strategy 1 — Liquidity Sweep + Market Structure Shift (ICT / SMC reversal).

The institutional playbook: price is driven beyond an obvious pool of retail stops
(equal lows / a prior swing low), those stops provide the liquidity for large
orders, and price then reverses. We only act once that reversal is *confirmed* by
a change of character (CHoCH), and we enter on the pullback into the optimal-trade-
entry (OTE) discount zone.

    Sell-side sweep  ->  bullish CHoCH  ->  OTE long
    Buy-side sweep   ->  bearish CHoCH  ->  OTE short
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .. import indicators as ind
from .. import smc
from .. import structure as st
from ..signal import Signal, build_targets, grade_for, round_p
from .base import MarketData, Strategy


class LiquiditySweepMSS(Strategy):
    key = "sweep"
    name = "Liquidity Sweep + MSS"
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
        ltf = md.ltf
        o, h, l, c = ltf.open, ltf.high, ltf.low, ltf.close
        v = ltf.volume
        price = md.price
        prec = md.precision
        long = side == "LONG"

        # --- 1. recent stop-hunt on the trigger timeframe (required) ---------
        sweep = smc.detect_sweep(h, l, c, window=6)
        if sweep is None:
            return None
        want_sweep = "sellside" if long else "buyside"
        if sweep.side != want_sweep:
            return None

        # --- 2. structure shift confirming the reversal (required) -----------
        breaks = st.structure_breaks(h, l, c)
        want_dir = "bullish" if long else "bearish"
        conf_break: Optional[st.Break] = None
        for b in breaks:
            if b.direction == want_dir and b.index >= sweep.index:
                conf_break = b  # keep the most recent qualifying break
        if conf_break is None:
            return None

        # --- 3. impulse leg from the sweep to the structure break ------------
        seg_lo = sweep.index
        seg_hi = conf_break.index + 1
        leg_low = float(np.min(l[seg_lo:seg_hi]))
        leg_high = float(np.max(h[seg_lo:seg_hi]))
        if leg_high <= leg_low:
            return None

        # --- 4. optimal-trade-entry (discount/premium) zone ------------------
        entry_low, entry_high = smc.ote_zone(leg_low, leg_high, side)
        entry_ref = (entry_low + entry_high) / 2.0

        atr_l = ind.last(ind.atr(h, l, c, 14), default=(leg_high - leg_low) * 0.1)
        buffer = max(0.3 * atr_l, (leg_high - leg_low) * 0.05)
        sl = (leg_low - buffer) if long else (leg_high + buffer)
        risk = abs(entry_ref - sl)
        if risk <= 0:
            return None
        if entry_ref and (risk / entry_ref * 100.0) > self.max_risk_pct:
            return None  # too wide to be a sniper entry

        # --- 5. freshness / actionability guard ------------------------------
        span = leg_high - leg_low
        if long:
            if not (price > sl and price < leg_high + span * 0.6):
                return None
        else:
            if not (price < sl and price > leg_low - span * 0.6):
                return None

        # --- 6. targets from opposing liquidity ------------------------------
        liq = self._target_liquidity(md, long)
        built = build_targets(side, entry_ref, sl, liq, prec)
        if built is None:
            return None
        tps, rrs = built

        # --- 7. scoring / confluences ----------------------------------------
        score = 4  # sweep (2) + structure shift (2), both required
        confirmations: List[str] = [
            f"Liquidity sweep of {'sell' if long else 'buy'}-side @ {round_p(sweep.level, prec)}",
            f"{conf_break.kind} {want_dir} on {ltf.interval} (broke {round_p(conf_break.level, prec)})",
        ]

        # HTF bias / premium-discount
        htf = md.htf
        htf_trend = st.trend(htf.high, htf.low)
        m = min(60, len(htf))
        htf_hi = float(np.max(htf.high[-m:]))
        htf_lo = float(np.min(htf.low[-m:]))
        htf_pd = smc.premium_discount(htf_lo, htf_hi, price)
        if long and htf_pd == "discount":
            score += 2
            confirmations.append(f"Price in HTF discount ({htf.interval})")
        elif (not long) and htf_pd == "premium":
            score += 2
            confirmations.append(f"Price in HTF premium ({htf.interval})")
        elif (long and htf_trend in ("bullish", "range")) or (not long and htf_trend in ("bearish", "range")):
            score += 1
            confirmations.append(f"HTF trend not opposing ({htf_trend})")

        # entry sits in discount (long) / premium (short) of the impulse
        leg_pd = smc.premium_discount(leg_low, leg_high, entry_ref)
        if (long and leg_pd == "discount") or (not long and leg_pd == "premium"):
            score += 1
            confirmations.append("Entry inside OTE (0.62-0.79)")

        # FVG left inside the impulse
        fvgs = smc.find_fvgs(h, l, c)
        want_fvg = "bullish" if long else "bearish"
        if any(want_fvg == f.type and seg_lo <= f.index <= seg_hi for f in fvgs):
            score += 1
            confirmations.append("Fair value gap in the impulse")

        # volume spike on the sweep candle
        vol_ma = ind.sma(v, 20)
        if sweep.index < vol_ma.size and np.isfinite(vol_ma[sweep.index]) and vol_ma[sweep.index] > 0:
            if v[sweep.index] > 1.5 * vol_ma[sweep.index]:
                score += 1
                confirmations.append("Volume spike on the sweep")

        # RSI stretched at the sweep
        rsi = ind.rsi(c, 14)
        if sweep.index < rsi.size and np.isfinite(rsi[sweep.index]):
            r = rsi[sweep.index]
            if (long and r < 40) or (not long and r > 60):
                score += 1
                confirmations.append(f"RSI reversal setup ({r:.0f})")

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
            htf=f"{htf_trend} bias, price in {htf_pd} of {htf.interval} range",
            mtf=f"{st.trend(md.mtf.high, md.mtf.low)} structure on {md.mtf.interval}",
            ltf=f"Sweep + {conf_break.kind} on {ltf.interval}",
            confirmations=confirmations,
            invalidation=(
                f"Close back {'below' if long else 'above'} {round_p(sl, prec)} "
                f"(sweep reclaimed)"
            ),
            price_precision=prec,
            atr=round_p(atr_l, prec),
            risk_pct=round(risk / entry_ref * 100.0, 2) if entry_ref else 0.0,
        )

    @staticmethod
    def _target_liquidity(md: MarketData, long: bool) -> List[float]:
        """Opposing liquidity pools (swing highs for longs, lows for shorts) across
        the trigger and structure timeframes."""
        levels: List[float] = []
        for cd in (md.ltf, md.mtf):
            highs, lows = smc.liquidity_levels(cd.high, cd.low)
            levels.extend(highs if long else lows)
        # include the extreme of the HTF window as a magnet
        m = min(60, len(md.htf))
        if long:
            levels.append(float(np.max(md.htf.high[-m:])))
        else:
            levels.append(float(np.min(md.htf.low[-m:])))
        return levels
