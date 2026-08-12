"""Runtime configuration.

Values are loaded from environment / .env at startup but the resulting object is
mutable so Telegram commands (e.g. /setminscore) can tune it live.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _s(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _i(name: str, default: int) -> int:
    try:
        return int(float(_s(name, str(default))))
    except (TypeError, ValueError):
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(_s(name, str(default)))
    except (TypeError, ValueError):
        return default


def _b(name: str, default: bool) -> bool:
    v = _s(name, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _list(name: str) -> List[str]:
    return [p.strip().upper() for p in _s(name, "").split(",") if p.strip()]


def _list_int(name: str) -> List[int]:
    out: List[int] = []
    for p in _s(name, "").split(","):
        p = p.strip()
        if p:
            try:
                out.append(int(p))
            except ValueError:
                pass
    return out


@dataclass
class Config:
    # Telegram
    token: str = field(default_factory=lambda: _s("TELEGRAM_BOT_TOKEN"))
    chat_id: str = field(default_factory=lambda: _s("TELEGRAM_CHAT_ID"))
    allowed_users: List[int] = field(default_factory=lambda: _list_int("ALLOWED_USER_IDS"))

    # Universe
    min_quote_volume: float = field(default_factory=lambda: _f("MIN_QUOTE_VOLUME", 10_000_000))
    exclude: List[str] = field(default_factory=lambda: _list("EXCLUDE_SYMBOLS") or ["USDCUSDT", "BTCDOMUSDT"])

    # Cadence
    scan_interval: int = field(default_factory=lambda: _i("SCAN_INTERVAL", 300))
    universe_refresh: int = field(default_factory=lambda: _i("UNIVERSE_REFRESH", 1800))
    concurrency: int = field(default_factory=lambda: _i("CONCURRENCY", 12))

    # Timeframes
    htf: str = field(default_factory=lambda: _s("TF_HTF", "4h"))
    mtf: str = field(default_factory=lambda: _s("TF_MTF", "1h"))
    ltf: str = field(default_factory=lambda: _s("TF_LTF", "15m"))
    kline_limit: int = field(default_factory=lambda: _i("KLINE_LIMIT", 300))

    # Gating
    min_score: int = field(default_factory=lambda: _i("MIN_SCORE", 6))
    cooldown: int = field(default_factory=lambda: _i("SIGNAL_COOLDOWN", 7200))
    max_signals_per_cycle: int = field(default_factory=lambda: _i("MAX_SIGNALS_PER_CYCLE", 8))

    # Trade tracker / monitor
    track_trades: bool = field(default_factory=lambda: _b("TRACK_TRADES", True))
    track_interval: int = field(default_factory=lambda: _i("TRACK_INTERVAL", 60))
    track_tf: str = field(default_factory=lambda: _s("TRACK_TF", "1m"))
    entry_timeout: int = field(default_factory=lambda: _i("ENTRY_TIMEOUT", 43200))
    move_sl_to_be: bool = field(default_factory=lambda: _b("MOVE_SL_TO_BE", True))

    # Runtime toggles
    muted: bool = False

    def validate(self) -> List[str]:
        """Return a list of fatal problems (empty == OK)."""
        problems = []
        if not self.token:
            problems.append("TELEGRAM_BOT_TOKEN is not set (.env).")
        if not self.chat_id:
            problems.append("TELEGRAM_CHAT_ID is not set (.env).")
        return problems

    def as_dict(self) -> dict:
        return {
            "min_quote_volume": self.min_quote_volume,
            "scan_interval": self.scan_interval,
            "universe_refresh": self.universe_refresh,
            "htf": self.htf,
            "mtf": self.mtf,
            "ltf": self.ltf,
            "min_score": self.min_score,
            "cooldown": self.cooldown,
            "max_signals_per_cycle": self.max_signals_per_cycle,
            "track_trades": self.track_trades,
            "track_interval": self.track_interval,
            "track_tf": self.track_tf,
            "entry_timeout": self.entry_timeout,
            "move_sl_to_be": self.move_sl_to_be,
            "muted": self.muted,
            "excluded": ",".join(self.exclude),
        }
