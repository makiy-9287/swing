#!/usr/bin/env python3
"""Offline unit test for the trade tracker — no network, no Telegram token.

Exercises the pure lifecycle engine (entry / TP1-3 / SL / break-even) and a full
Store round-trip through /pnl formatting.

Usage:  python test_tracker.py
"""
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sniper import formatter as fmt
from sniper.signal import Signal
from sniper.store import Store
from sniper.tracker import TradeEvent, evaluate, pnl_r, unrealized_r

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def make_trade(side="LONG", **over):
    """A fresh PENDING trade dict, mirroring a DB row."""
    if side == "LONG":
        t = dict(entry_low=99.5, entry_high=100.5, entry_ref=100.0, sl=98.0,
                 tp1=102.0, tp2=104.0, tp3=106.0)
    else:
        t = dict(entry_low=99.5, entry_high=100.5, entry_ref=100.0, sl=102.0,
                 tp1=98.0, tp2=96.0, tp3=94.0)
    t.update(side=side, rr1=1.0, rr2=2.0, rr3=3.0, precision=2,
             status="PENDING", entry_at=None,
             tp1_at=None, tp2_at=None, tp3_at=None, sl_at=None,
             last_check_ms=0, symbol="TESTUSDT", strategy_key="sweep")
    t.update(over)
    return t


def kinds(events):
    return [k for k, _ in events]


# ---------------------------------------------------------------- pure engine
def test_long_full_win():
    print("\nLONG — entry then TP1/TP2/TP3 (full win)")
    t = make_trade("LONG")
    candles = [
        (1000, 101.0, 101.5, 100.2, 100.3),   # touches zone -> ENTRY
        (2000, 100.3, 102.5, 100.1, 102.2),   # TP1
        (3000, 102.2, 106.5, 102.0, 106.3),   # TP2 + TP3 -> WIN
    ]
    changes, ev = evaluate(t, candles)
    check("events ENTRY,TP1,TP2,TP3", kinds(ev) == ["ENTRY", "TP1", "TP2", "TP3"])
    check("status CLOSED", changes["status"] == "CLOSED")
    check("outcome WIN", changes["outcome"] == "WIN")
    check("pnl_r == 2.0", abs(changes["pnl_r"] - 2.0) < 1e-9)


def test_long_straight_loss():
    print("\nLONG — entry then straight to SL (loss)")
    t = make_trade("LONG")
    candles = [
        (1000, 100.0, 100.5, 99.6, 99.8),     # ENTRY
        (2000, 99.8, 99.9, 97.5, 97.6),       # SL, no TP
    ]
    changes, ev = evaluate(t, candles)
    check("events ENTRY,SL", kinds(ev) == ["ENTRY", "SL"])
    check("outcome LOSS", changes["outcome"] == "LOSS")
    check("pnl_r == -1.0", abs(changes["pnl_r"] + 1.0) < 1e-9)


def test_long_be_after_tp1():
    print("\nLONG — TP1 then reverse to break-even (small win)")
    t = make_trade("LONG")
    candles = [
        (1000, 101.0, 101.5, 100.2, 100.3),   # ENTRY
        (2000, 100.3, 102.5, 100.1, 102.2),   # TP1 (SL -> BE)
        (3000, 102.0, 102.1, 99.9, 100.0),    # back to entry -> BE stop
    ]
    changes, ev = evaluate(t, candles)
    check("events ENTRY,TP1,SL", kinds(ev) == ["ENTRY", "TP1", "SL"])
    check("outcome WIN (TP1 banked)", changes["outcome"] == "WIN")
    check("pnl_r ~ 0.333", abs(changes["pnl_r"] - round(1.0 / 3, 3)) < 1e-6)


def test_be_disabled_tp1_then_sl_loses_r():
    print("\nLONG — TP1 then SL with BE disabled (negative blended R)")
    t = make_trade("LONG")
    candles = [
        (1000, 101.0, 101.5, 100.2, 100.3),
        (2000, 100.3, 102.5, 100.1, 102.2),   # TP1
        (3000, 102.0, 102.1, 97.5, 97.6),     # to SL
    ]
    changes, ev = evaluate(t, candles, move_sl_to_be=False)
    # banked rr1=1, remaining 2 * -1R  => (1 - 2)/3
    check("pnl_r ~ -0.333", abs(changes["pnl_r"] - round(-1.0 / 3, 3)) < 1e-6)
    check("still classed WIN (reached TP1)", changes["outcome"] == "WIN")


def test_ambiguous_candle_sl_first():
    print("\nLONG — same candle spans TP1 and SL, open equidistant -> SL first")
    t = make_trade("LONG", status="ACTIVE", entry_at=500, last_check_ms=500)
    candles = [(1000, 100.0, 102.5, 97.5, 98.0)]   # o=100: |o-sl|=2 == |tp1-o|=2
    changes, ev = evaluate(t, candles)
    check("SL resolved first", kinds(ev) == ["SL"])
    check("outcome LOSS", changes["outcome"] == "LOSS")


def test_pending_no_fill():
    print("\nLONG — price drops past the zone without touching it (no entry)")
    t = make_trade("LONG")
    # candle entirely below the entry zone (high 99.0 < entry_low 99.5)
    candles = [(1000, 99.0, 99.0, 97.0, 97.5)]
    changes, ev = evaluate(t, candles)
    check("no events", ev == [])
    check("stays PENDING", changes.get("status") is None)
    check("last_check advanced", changes["last_check_ms"] == 1000)


def test_short_full_win():
    print("\nSHORT — entry then TP1/TP2/TP3 (full win)")
    t = make_trade("SHORT")
    candles = [
        (1000, 99.0, 99.8, 98.5, 99.0),       # touches zone -> ENTRY
        (2000, 99.0, 99.2, 97.9, 98.0),       # low<=98 -> TP1
        (3000, 98.0, 98.1, 93.5, 94.0),       # TP2 + TP3 -> WIN
    ]
    changes, ev = evaluate(t, candles)
    check("events ENTRY,TP1,TP2,TP3", kinds(ev) == ["ENTRY", "TP1", "TP2", "TP3"])
    check("outcome WIN", changes["outcome"] == "WIN")
    check("pnl_r == 2.0", abs(changes["pnl_r"] - 2.0) < 1e-9)


def test_unrealized_r():
    print("\nUnrealized R for a running ACTIVE trade")
    t = make_trade("LONG", status="ACTIVE", last_price=101.0)  # +0.5R at price
    ur = unrealized_r(t)
    check("unreal ~ +0.5R (no TP banked)", abs(ur - 0.5) < 1e-9)


# ---------------------------------------------------------------- store + fmt
def test_store_roundtrip():
    print("\nStore round-trip + /pnl formatting")
    path = os.path.join("data", "test_tracker.sqlite")
    if os.path.exists(path):
        os.remove(path)
    store = Store(path=path)

    sig = Signal(
        strategy="Liquidity Sweep + MSS", strategy_key="sweep",
        symbol="TESTUSDT", side="LONG", score=8, max_score=10, grade="A",
        entry_low=99.5, entry_high=100.5, entry_ref=100.0, sl=98.0,
        tps=[102.0, 104.0, 106.0], rr=[1.0, 2.0, 3.0],
        htf="up", mtf="up", ltf="trigger", confirmations=["a", "b"],
        invalidation="below 98", price_precision=2, atr=1.0, risk_pct=2.0,
        created_at=time.time() - 10,
    )
    tid = store.open_trade(sig, signal_id=1)
    check("trade opened", tid is not None)
    check("duplicate open blocked", store.open_trade(sig, 1) is None)
    check("has_open_trade", store.has_open_trade(sig.key) is True)

    # drive it to a full win using the same synthetic candles
    now_ms = int((time.time()) * 1000)
    candles = [
        (now_ms - 3000, 101.0, 101.5, 100.2, 100.3),
        (now_ms - 2000, 100.3, 102.5, 100.1, 102.2),
        (now_ms - 1000, 102.2, 106.5, 102.0, 106.3),
    ]
    t = store.open_trades()[0]
    t["last_check_ms"] = 0
    changes, ev = evaluate(t, candles)
    changes["last_price"] = 106.3
    store.apply_trade_update(t["id"], changes)

    summ = store.pnl_summary()
    check("summary wins == 1", summ["wins"] == 1)
    check("summary win_rate == 100", abs(summ["win_rate"] - 100.0) < 1e-9)
    check("summary tp3 == 1", summ["tp3"] == 1)
    check("no more open trades", summ["pending"] + summ["active"] == 0)

    running = store.open_trades()
    closed = store.recent_closed()
    out = fmt.format_pnl(summ, running, closed, "all-time")
    check("format_pnl renders WIN", "WIN" in out and "Win rate" in out)

    snap = closed[0]
    for kind in ("ENTRY", "TP1", "TP3", "SL", "EXPIRE"):
        msg = fmt.format_trade_event(TradeEvent(kind, snap))
        check(f"format_trade_event({kind}) non-empty", bool(msg) and "TESTUSDT" in msg)

    store.close()
    os.remove(path)


def main():
    for fn in (
        test_long_full_win, test_long_straight_loss, test_long_be_after_tp1,
        test_be_disabled_tp1_then_sl_loses_r, test_ambiguous_candle_sl_first,
        test_pending_no_fill, test_short_full_win, test_unrealized_r,
        test_store_roundtrip,
    ):
        fn()
    print(f"\n{'='*40}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
