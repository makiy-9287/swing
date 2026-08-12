"""Shared strategy scaffolding."""
from __future__ import annotations

from dataclasses import dataclass

from ..candles import Candles


@dataclass
class MarketData:
    """Everything a strategy needs to analyse one symbol."""
    symbol: str
    precision: int
    htf: Candles   # higher-timeframe (bias)
    mtf: Candles   # structure / zone timeframe
    ltf: Candles   # entry-trigger timeframe

    @property
    def price(self) -> float:
        return self.ltf.last_price


class Strategy:
    key: str = "base"
    name: str = "Base"

    # minimum candles required on each timeframe to analyse safely
    min_htf: int = 60
    min_mtf: int = 80
    min_ltf: int = 120

    # reject setups whose stop is further than this % from entry (imprecise / too wide)
    max_risk_pct: float = 4.0

    def ready(self, md: MarketData) -> bool:
        return (
            md.htf.enough(self.min_htf)
            and md.mtf.enough(self.min_mtf)
            and md.ltf.enough(self.min_ltf)
        )

    def analyze(self, md: MarketData):  # -> Optional[Signal]
        raise NotImplementedError
