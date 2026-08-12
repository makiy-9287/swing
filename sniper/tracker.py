"""Trade tracker / monitor.

Every signal the bot *broadcasts* is registered as a tracked paper trade. This
module watches the market for each open trade and resolves its lifecycle:

    PENDING  --(price touches entry zone)-->  ACTIVE
    ACTIVE   --(TP1 -> TP2 -> TP3)-->         CLOSED (WIN)
    ACTIVE   --(SL)-->                        CLOSED (WIN if a TP banked, else LOSS)
    PENDING  --(entry never fills in time)--> CANCELLED

Detection runs on *closed* candles of ``cfg.track_tf`` (1m by default), so a
single wick that pierces a level is caught even between polls. The heavy lifting
lives in the pure, side-effect-free :func:`evaluate` so it can be unit-tested
offline without any network or database.

Fill/PnL model (documented, deliberately simple — this is a signals tracker,
not an execution engine):
  * Entry is assumed filled at ``entry_ref`` (the published plan entry).
  * The position is three equal thirds, scaled out at TP1 / TP2 / TP3.
  * With ``move_sl_to_be`` (default on) the stop moves to break-even once TP1 is
    banked, so a trade that reaches TP1 can never become a loss.
  * A trade counts as a WIN if it reaches at least TP1 before its stop, a LOSS
    if the stop is hit first. Realised R is the blended R of the three thirds.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("sniper.tracker")


# --------------------------------------------------------------------- events
@dataclass
class TradeEvent:
    kind: str            # ENTRY | TP1 | TP2 | TP3 | SL | EXPIRE
    trade: dict          # post-update snapshot of the trade (for messaging)
    ts: float = 0.0      # event time (candle open ms, or wall-clock for EXPIRE)


# --------------------------------------------------------------------- helpers
def pnl_r(tp_hits: List[bool], rrs: List[float], sl_hit: bool,
          move_sl_to_be: bool) -> float:
    """Realised R for a *closed* trade given which TPs banked and whether the
    stop was taken. Position = three equal thirds."""
    if all(tp_hits):                       # full run to TP3
        return round(sum(rrs) / 3.0, 3)
    if not sl_hit:                         # shouldn't happen for a closed trade
        return 0.0
    banked = sum(rrs[i] for i in range(3) if tp_hits[i])
    remaining = sum(1 for i in range(3) if not tp_hits[i])
    stop_r = 0.0 if (move_sl_to_be and tp_hits[0]) else -1.0
    return round((banked + remaining * stop_r) / 3.0, 3)


def unrealized_r(trade: dict) -> Optional[float]:
    """Mark-to-market R for a running trade at its last observed price. Banked
    thirds are locked at their TP R; the open remainder is marked to price."""
    if trade["status"] != "ACTIVE":
        return None
    price = trade.get("last_price")
    if price is None:
        return None
    entry, sl = trade["entry_ref"], trade["sl"]
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    long = trade["side"] == "LONG"
    live_r = (price - entry) / risk if long else (entry - price) / risk
    rrs = [trade["rr1"], trade["rr2"], trade["rr3"]]
    hits = [trade["tp1_at"], trade["tp2_at"], trade["tp3_at"]]
    banked = sum(rrs[i] for i in range(3) if hits[i])
    remaining = sum(1 for i in range(3) if not hits[i])
    return round((banked + remaining * live_r) / 3.0, 3)


def evaluate(trade: dict, candles: List[Tuple[int, float, float, float, float]],
             move_sl_to_be: bool = True) -> Tuple[dict, List[Tuple[str, float]]]:
    """Advance one open trade over new candles.

    ``candles`` are ``(open_time_ms, open, high, low, close)`` tuples in
    ascending time order (closed candles only). Only candles newer than the
    trade's ``last_check_ms`` are considered.

    Returns ``(changes, events)`` where ``changes`` is a dict of DB columns to
    update and ``events`` is a list of ``(kind, ts)`` in the order they fired.
    Pure: it never touches the DB, clock or network.
    """
    long = trade["side"] == "LONG"
    entry_low, entry_high = trade["entry_low"], trade["entry_high"]
    entry_ref, sl = trade["entry_ref"], trade["sl"]
    tps = [trade["tp1"], trade["tp2"], trade["tp3"]]
    rrs = [trade["rr1"], trade["rr2"], trade["rr3"]]
    last_check = trade["last_check_ms"] or 0

    entered = trade["status"] == "ACTIVE"
    tp_at: List[Optional[float]] = [trade["tp1_at"], trade["tp2_at"], trade["tp3_at"]]
    entry_at = trade["entry_at"]
    sl_at: Optional[float] = None
    closed = False
    outcome = ""
    closed_at: Optional[float] = None

    events: List[Tuple[str, float]] = []
    new_last_check = last_check

    for (t, o, h, l, c) in candles:
        if t <= last_check:
            continue
        new_last_check = max(new_last_check, t)

        # --- entry fill: does this candle touch the entry zone? ---
        if not entered:
            if (l <= entry_high) and (h >= entry_low):
                entered = True
                entry_at = t
                events.append(("ENTRY", t))
            else:
                continue  # still waiting for a pullback into the zone

        # --- resolve TP/SL touches within this candle ---
        # BE only takes effect on candles *after* TP1 banked, so use the stop
        # that was in force at the start of this candle.
        eff_sl = entry_ref if (move_sl_to_be and tp_at[0] is not None) else sl

        while not closed:
            nxt = next((i for i in range(3) if tp_at[i] is None), None)
            sl_in = (l <= eff_sl) if long else (h >= eff_sl)
            tp_in = nxt is not None and (
                (h >= tps[nxt]) if long else (l <= tps[nxt]))

            if not sl_in and not tp_in:
                break

            take_sl = False
            if sl_in and tp_in:
                # ambiguous candle: assume the level nearer the open filled first
                take_sl = abs(o - eff_sl) <= abs(tps[nxt] - o)
            elif sl_in:
                take_sl = True

            if take_sl:
                sl_at = t
                closed = True
                closed_at = t
                outcome = "WIN" if tp_at[0] is not None else "LOSS"
                events.append(("SL", t))
                break

            # otherwise the next take-profit filled
            tp_at[nxt] = t
            events.append((f"TP{nxt + 1}", t))
            if nxt == 2:                    # final target reached -> win
                closed = True
                closed_at = t
                outcome = "WIN"

        if closed:
            break

    # ---------------------------------------------------------- build changes
    changes: dict = {"last_check_ms": int(new_last_check)}
    if entered and trade["status"] == "PENDING":
        changes["status"] = "ACTIVE"
        changes["entry_at"] = entry_at
    for i in range(3):
        col = f"tp{i + 1}_at"
        if tp_at[i] is not None and not trade.get(col):
            changes[col] = tp_at[i]
    if closed:
        changes["status"] = "CLOSED"
        changes["closed_at"] = closed_at
        changes["outcome"] = outcome
        if sl_at is not None:
            changes["sl_at"] = sl_at
        changes["pnl_r"] = pnl_r(
            [tp_at[0] is not None, tp_at[1] is not None, tp_at[2] is not None],
            rrs, sl_at is not None, move_sl_to_be,
        )
    return changes, events


# --------------------------------------------------------------------- engine
def _rows(candles) -> List[Tuple[int, float, float, float, float]]:
    if candles is None or len(candles) == 0:
        return []
    return list(zip(
        candles.open_time.tolist(),
        candles.open.tolist(),
        candles.high.tolist(),
        candles.low.tolist(),
        candles.close.tolist(),
    ))


class Tracker:
    """Polls open trades and returns the state-change events to broadcast."""

    def __init__(self, client, cfg, store):
        self.client = client
        self.cfg = cfg
        self.store = store

    async def poll(self, now: Optional[float] = None) -> List[TradeEvent]:
        now = time.time() if now is None else now
        trades = self.store.open_trades()
        if not trades:
            return []

        by_symbol: Dict[str, List[dict]] = {}
        for t in trades:
            by_symbol.setdefault(t["symbol"], []).append(t)

        events: List[TradeEvent] = []
        for symbol, group in by_symbol.items():
            oldest = min((t["last_check_ms"] or 0) for t in group)
            need = (int(now * 1000) - oldest) // 60000 + 3
            limit = int(min(1000, max(2, need)))
            try:
                candles = await self.client.klines(symbol, self.cfg.track_tf, limit)
            except Exception as e:
                log.error("tracker klines failed for %s: %s", symbol, e)
                continue
            rows = _rows(candles)
            last_price = candles.last_price if (candles and len(candles)) else None

            for t in group:
                # entry never filled within the timeout -> cancel
                if (t["status"] == "PENDING"
                        and (now - t["created_at"]) > self.cfg.entry_timeout):
                    self.store.apply_trade_update(t["id"], {
                        "status": "CANCELLED", "closed_at": now,
                        "last_check_ms": int(now * 1000),
                    })
                    events.append(TradeEvent("EXPIRE", dict(t), ts=now))
                    continue
                if not rows:
                    continue

                changes, evs = evaluate(t, rows, move_sl_to_be=self.cfg.move_sl_to_be)
                if last_price is not None:
                    changes["last_price"] = last_price
                self.store.apply_trade_update(t["id"], changes)

                snap = dict(t)
                snap.update(changes)
                for kind, ts in evs:
                    events.append(TradeEvent(kind, snap, ts=ts))

        return events
