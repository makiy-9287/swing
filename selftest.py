#!/usr/bin/env python3
"""Offline self-test — exercises the full engine against LIVE Binance data
without needing a Telegram token. Verifies:
  * connectivity + universe build
  * data fetch on all 3 timeframes
  * both strategies run without exceptions across many symbols
  * every emitted signal is internally consistent (SL/TP geometry, ordering)

Usage:  python selftest.py [max_symbols]
"""
import asyncio
import sys
import time

try:  # Windows consoles default to cp1252 and choke on emoji
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sniper.client import BinanceFutures
from sniper.config import Config
from sniper.formatter import format_signal
from sniper.scanner import Scanner
from sniper.store import Store


def check_signal(s) -> list:
    """Return a list of integrity problems for a signal (empty == valid)."""
    problems = []
    if s.side == "LONG":
        if not (s.sl < s.entry_low <= s.entry_high):
            problems.append("SL not below entry zone")
        if not (s.tps[0] >= s.entry_high):
            problems.append("TP1 inside/below entry zone (long)")
        if not (s.tps[0] < s.tps[1] < s.tps[2]):
            problems.append("TPs not ascending")
    else:
        if not (s.sl > s.entry_high >= s.entry_low):
            problems.append("SL not above entry zone")
        if not (s.tps[0] <= s.entry_low):
            problems.append("TP1 inside/above entry zone (short)")
        if not (s.tps[0] > s.tps[1] > s.tps[2]):
            problems.append("TPs not descending")
    if not (0 <= s.score <= s.max_score):
        problems.append("score out of range")
    if s.risk_pct <= 0 or s.risk_pct > 4.0:
        problems.append(f"risk_pct out of bounds ({s.risk_pct})")
    if s.rr[0] < 0.95:
        problems.append(f"TP1 below 1R ({s.rr[0]})")
    if len(s.tps) != 3 or len(s.rr) != 3:
        problems.append("expected exactly 3 TPs")
    return problems


async def main():
    max_symbols = int(sys.argv[1]) if len(sys.argv) > 1 else 40

    cfg = Config()
    cfg.min_score = 0  # capture everything for testing
    client = BinanceFutures(concurrency=cfg.concurrency)
    await client.start()
    store = Store(path="data/selftest.sqlite")
    scanner = Scanner(client, cfg, store)

    t0 = time.time()
    print("Building universe…")
    await scanner.ensure_universe(force=True)
    syms = scanner.universe.symbols[:max_symbols]
    print(f"Universe: {len(scanner.universe.symbols)} symbols "
          f"(testing first {len(syms)})  [{time.time()-t0:.1f}s]")

    print("Scanning…")
    t1 = time.time()
    tasks = [scanner._scan_symbol(s, respect_gates=False, floor=0) for s in syms]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = 0
    signals = []
    for sym, r in zip(syms, results):
        if isinstance(r, Exception):
            errors += 1
            print(f"  ERROR {sym}: {r!r}")
            continue
        signals.extend(r)

    print(f"Scan of {len(syms)} symbols done in {time.time()-t1:.1f}s "
          f"— {len(signals)} raw setups, {errors} errors")

    bad = 0
    for s in signals:
        problems = check_signal(s)
        if problems:
            bad += 1
            print(f"  INVALID {s.symbol} {s.side} {s.strategy_key}: {problems}")

    signals.sort(key=lambda s: s.score, reverse=True)
    print(f"\nIntegrity: {len(signals)-bad}/{len(signals)} signals valid.")
    if signals:
        print("\n===== Highest-conviction sample =====\n")
        print(format_signal(signals[0]).replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", "").replace("<code>", "")
              .replace("</code>", ""))

    await client.close()
    store.close()

    ok = errors == 0 and bad == 0
    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
