"""The scan engine: fetch multi-timeframe data for every symbol in the universe,
run both strategies, and return fresh, de-duplicated signals ranked by conviction.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Tuple

from .candles import Candles
from .client import BinanceFutures
from .config import Config
from .store import Store
from .strategies import STRATEGIES
from .strategies.base import MarketData
from .signal import Signal
from .universe import Universe, build_universe

log = logging.getLogger("sniper.scanner")


class Scanner:
    def __init__(self, client: BinanceFutures, cfg: Config, store: Store):
        self.client = client
        self.cfg = cfg
        self.store = store
        self.universe = Universe()
        self.last_scan_at: float = 0.0
        self.last_scan_count: int = 0
        self.last_signal_count: int = 0

    # --------------------------------------------------------------- universe
    async def ensure_universe(self, force: bool = False) -> None:
        stale = (time.time() - self.universe.updated_at) > self.cfg.universe_refresh
        if force or stale or not self.universe.symbols:
            uni = await build_universe(self.client, self.cfg)
            if uni.symbols:  # only replace on success
                self.universe = uni

    # --------------------------------------------------------------- fetching
    async def _fetch_md(self, symbol: str) -> Optional[MarketData]:
        htf, mtf, ltf = await asyncio.gather(
            self.client.klines(symbol, self.cfg.htf, self.cfg.kline_limit),
            self.client.klines(symbol, self.cfg.mtf, self.cfg.kline_limit),
            self.client.klines(symbol, self.cfg.ltf, self.cfg.kline_limit),
        )
        if not htf or not mtf or not ltf:
            return None
        return MarketData(
            symbol=symbol,
            precision=self.universe.precision(symbol),
            htf=htf,
            mtf=mtf,
            ltf=ltf,
        )

    def _run_strategies(self, md: MarketData) -> List[Signal]:
        out: List[Signal] = []
        for strat in STRATEGIES:
            try:
                sig = strat.analyze(md)
            except Exception as e:  # a bug in one strategy must not kill the scan
                log.exception("Strategy %s failed on %s: %s", strat.key, md.symbol, e)
                continue
            if sig is not None:
                out.append(sig)
        return out

    async def _scan_symbol(self, symbol: str, respect_gates: bool = True,
                           floor: int = 0) -> List[Signal]:
        try:
            md = await self._fetch_md(symbol)
        except Exception as e:
            log.error("Fetch failed for %s: %s", symbol, e)
            return []
        if md is None:
            return []
        signals = self._run_strategies(md)
        fresh: List[Signal] = []
        for s in signals:
            if respect_gates:
                if s.score < self.cfg.min_score:
                    continue
                if self.store.in_cooldown(s.key, self.cfg.cooldown):
                    continue
            elif s.score < floor:
                continue
            fresh.append(s)
        return fresh

    async def _collect(self, respect_gates: bool, floor: int = 0) -> List[Signal]:
        await self.ensure_universe()
        symbols = list(self.universe.symbols)
        if not symbols:
            log.warning("Empty universe — skipping scan")
            return []

        tasks = [asyncio.create_task(self._scan_symbol(s, respect_gates, floor))
                 for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: List[Signal] = []
        for r in results:
            if isinstance(r, Exception):
                log.error("scan task error: %s", r)
                continue
            signals.extend(r)
        signals.sort(key=lambda s: (s.score, s.rr[0] if s.rr else 0), reverse=True)
        self.last_scan_at = time.time()
        self.last_scan_count = len(symbols)
        return signals

    # --------------------------------------------------------------- public
    async def scan(self) -> List[Signal]:
        """Scheduled scan: fully gated (score + cooldown), capped burst."""
        signals = (await self._collect(respect_gates=True))[: self.cfg.max_signals_per_cycle]
        self.last_signal_count = len(signals)
        log.info("Scan complete: %d symbols, %d signals",
                 self.last_scan_count, len(signals))
        return signals

    async def snapshot(self, limit: int = 5, floor: int = 4) -> List[Signal]:
        """On-demand 'what's forming now' — ignores cooldown, keeps top setups."""
        signals = await self._collect(respect_gates=False, floor=floor)
        return signals[:limit]

    async def diagnose(self, symbol: str) -> Tuple[List[Signal], str]:
        """For the /scan command: return any signals plus a context summary,
        regardless of score/cooldown gating."""
        from . import structure as st

        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        md = await self._fetch_md(symbol)
        if md is None:
            return [], f"Could not fetch data for {symbol} (unknown symbol?)."

        sigs = self._run_strategies(md)
        htf_t = st.trend(md.htf.high, md.htf.low)
        mtf_t = st.trend(md.mtf.high, md.mtf.low)
        ltf_t = st.trend(md.ltf.high, md.ltf.low)
        context = (
            f"<b>{symbol}</b> @ <code>{md.price:.{md.precision}f}</code>\n"
            f"HTF {md.htf.interval}: {htf_t} · MTF {md.mtf.interval}: {mtf_t} · "
            f"LTF {md.ltf.interval}: {ltf_t}"
        )
        return sigs, context
