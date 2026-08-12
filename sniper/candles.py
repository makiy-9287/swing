"""Immutable OHLCV container backed by NumPy arrays.

Everything downstream (indicators, structure, SMC) operates on plain float64
arrays for speed and to avoid any pandas version-specific behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Candles:
    symbol: str
    interval: str
    open_time: np.ndarray  # int64 ms
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray  # quote volume
    close_time: np.ndarray  # int64 ms

    def __len__(self) -> int:
        return int(self.close.shape[0])

    @property
    def last_price(self) -> float:
        return float(self.close[-1])

    @classmethod
    def from_klines(cls, symbol: str, interval: str, raw: list) -> "Candles":
        """Build from Binance /fapi/v1/klines response, dropping the still-forming
        last candle so all analysis runs on *closed* candles only."""
        if not raw:
            return cls(symbol, interval, *[np.array([], dtype=np.float64)] * 7)  # type: ignore[arg-type]

        # The final row is the in-progress candle; exclude it.
        rows = raw[:-1] if len(raw) > 1 else raw
        arr = np.array(rows, dtype=object)

        def col(i, dtype=np.float64):
            return np.asarray(arr[:, i], dtype=dtype)

        return cls(
            symbol=symbol,
            interval=interval,
            open_time=col(0, np.int64),
            open=col(1),
            high=col(2),
            low=col(3),
            close=col(4),
            volume=col(7),  # quote asset volume
            close_time=col(6, np.int64),
        )

    def enough(self, n: int) -> bool:
        return len(self) >= n
