"""Symbol universe management.

Builds and caches the set of tradable USDT-perpetuals whose 24h quote volume
clears the configured threshold, plus per-symbol price precision for clean
rounding of levels in signals.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List

from .client import BinanceFutures
from .config import Config

log = logging.getLogger("sniper.universe")


@dataclass
class SymbolMeta:
    symbol: str
    price_precision: int
    quote_volume: float
    last_price: float


@dataclass
class Universe:
    symbols: List[str] = field(default_factory=list)
    meta: Dict[str, SymbolMeta] = field(default_factory=dict)
    updated_at: float = 0.0

    def precision(self, symbol: str) -> int:
        m = self.meta.get(symbol)
        return m.price_precision if m else 4


async def build_universe(client: BinanceFutures, cfg: Config) -> Universe:
    """Fetch exchange info + 24h tickers and intersect into a filtered universe."""
    info = await client.exchange_info()
    tickers = await client.ticker_24h()
    if not info or not tickers:
        log.error("Could not fetch exchangeInfo/ticker — keeping previous universe")
        return Universe()

    # Perpetual USDT symbols that are actively trading.
    precision: Dict[str, int] = {}
    tradable = set()
    for s in info.get("symbols", []):
        if (
            s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        ):
            tradable.add(s["symbol"])
            precision[s["symbol"]] = int(s.get("pricePrecision", 4))

    exclude = set(cfg.exclude)
    universe = Universe(updated_at=time.time())
    for t in tickers:
        sym = t.get("symbol", "")
        if sym not in tradable or sym in exclude:
            continue
        try:
            qv = float(t.get("quoteVolume", 0.0))
            lp = float(t.get("lastPrice", 0.0))
        except (TypeError, ValueError):
            continue
        if qv < cfg.min_quote_volume:
            continue
        universe.meta[sym] = SymbolMeta(sym, precision.get(sym, 4), qv, lp)

    # Rank by liquidity — most-traded symbols get scanned first each cycle.
    universe.symbols = sorted(
        universe.meta.keys(),
        key=lambda s: universe.meta[s].quote_volume,
        reverse=True,
    )
    log.info("Universe: %d symbols above %.0f USDT 24h volume",
             len(universe.symbols), cfg.min_quote_volume)
    return universe
