"""SQLite persistence: signal de-duplication (cooldowns), signal history and
lightweight analytics for the /stats command."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import List, Optional

from .signal import Signal

DEFAULT_PATH = os.path.join("data", "sniper.sqlite")


class Store:
    def __init__(self, path: str = DEFAULT_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock, self._db:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, symbol TEXT, strategy TEXT, strategy_key TEXT,
                    side TEXT, grade TEXT, score INTEGER,
                    entry_ref REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
                    payload TEXT
                )"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS cooldowns (
                    key TEXT PRIMARY KEY, ts REAL
                )"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER,
                    key TEXT,
                    symbol TEXT, strategy TEXT, strategy_key TEXT,
                    side TEXT, grade TEXT, score INTEGER, precision INTEGER,
                    entry_low REAL, entry_high REAL, entry_ref REAL, sl REAL,
                    tp1 REAL, tp2 REAL, tp3 REAL,
                    rr1 REAL, rr2 REAL, rr3 REAL, risk_pct REAL,
                    created_at REAL,
                    status TEXT,               -- PENDING | ACTIVE | CLOSED | CANCELLED
                    entry_at REAL, tp1_at REAL, tp2_at REAL, tp3_at REAL,
                    sl_at REAL, closed_at REAL,
                    outcome TEXT,              -- WIN | LOSS | (null while open)
                    pnl_r REAL,
                    last_check_ms INTEGER, last_price REAL
                )"""
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)"
            )

    # -------------------------------------------------- de-duplication
    def in_cooldown(self, key: str, cooldown: int) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT ts FROM cooldowns WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return False
        return (time.time() - float(row["ts"])) < cooldown

    def touch_cooldown(self, key: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO cooldowns(key, ts) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET ts = excluded.ts",
                (key, time.time()),
            )

    # -------------------------------------------------- history
    def record(self, s: Signal) -> int:
        payload = json.dumps({
            "entry_low": s.entry_low, "entry_high": s.entry_high,
            "rr": s.rr, "confirmations": s.confirmations,
            "htf": s.htf, "mtf": s.mtf, "ltf": s.ltf,
            "risk_pct": s.risk_pct,
        })
        with self._lock, self._db:
            cur = self._db.execute(
                """INSERT INTO signals
                   (ts, symbol, strategy, strategy_key, side, grade, score,
                    entry_ref, sl, tp1, tp2, tp3, payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    s.created_at, s.symbol, s.strategy, s.strategy_key, s.side,
                    s.grade, s.score, s.entry_ref, s.sl,
                    s.tps[0], s.tps[1], s.tps[2], payload,
                ),
            )
            signal_id = cur.lastrowid
        self.touch_cooldown(s.key)
        return signal_id

    def recent(self, limit: int = 10) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def stats(self) -> dict:
        since = time.time() - 86400
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]
            today = self._db.execute(
                "SELECT COUNT(*) n FROM signals WHERE ts >= ?", (since,)
            ).fetchone()["n"]
            by_strategy = self._db.execute(
                "SELECT strategy, COUNT(*) n FROM signals GROUP BY strategy"
            ).fetchall()
            by_grade = self._db.execute(
                "SELECT grade, COUNT(*) n FROM signals GROUP BY grade"
            ).fetchall()
        return {
            "total": total,
            "today": today,
            "by_strategy": {r["strategy"]: r["n"] for r in by_strategy},
            "by_grade": {r["grade"]: r["n"] for r in by_grade},
        }

    # -------------------------------------------------- trade tracker
    # columns the tracker is allowed to update on an open trade
    _TRADE_UPDATABLE = {
        "status", "entry_at", "tp1_at", "tp2_at", "tp3_at", "sl_at",
        "closed_at", "outcome", "pnl_r", "last_check_ms", "last_price",
    }

    def has_open_trade(self, key: str) -> bool:
        """True if a PENDING/ACTIVE trade already exists for this signal key."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM trades WHERE key = ? AND status IN "
                "('PENDING','ACTIVE') LIMIT 1",
                (key,),
            ).fetchone()
        return row is not None

    def open_trade(self, s: Signal, signal_id: Optional[int] = None) -> Optional[int]:
        """Register a broadcast signal as a tracked (paper) trade. Skips if an
        open trade for the same symbol+strategy+side is already live."""
        if self.has_open_trade(s.key):
            return None
        with self._lock, self._db:
            cur = self._db.execute(
                """INSERT INTO trades
                   (signal_id, key, symbol, strategy, strategy_key, side, grade,
                    score, precision, entry_low, entry_high, entry_ref, sl,
                    tp1, tp2, tp3, rr1, rr2, rr3, risk_pct, created_at,
                    status, last_check_ms, last_price)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_id, s.key, s.symbol, s.strategy, s.strategy_key, s.side,
                    s.grade, s.score, s.price_precision,
                    s.entry_low, s.entry_high, s.entry_ref, s.sl,
                    s.tps[0], s.tps[1], s.tps[2],
                    s.rr[0], s.rr[1], s.rr[2], s.risk_pct, s.created_at,
                    "PENDING", int(s.created_at * 1000), s.entry_ref,
                ),
            )
            return cur.lastrowid

    def open_trades(self) -> List[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM trades WHERE status IN ('PENDING','ACTIVE') "
                "ORDER BY created_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def apply_trade_update(self, trade_id: int, changes: dict) -> None:
        cols = [c for c in changes if c in self._TRADE_UPDATABLE]
        if not cols:
            return
        assignments = ", ".join(f"{c} = ?" for c in cols)
        values = [changes[c] for c in cols]
        values.append(trade_id)
        with self._lock, self._db:
            self._db.execute(
                f"UPDATE trades SET {assignments} WHERE id = ?", values
            )

    def recent_closed(self, limit: int = 8, since: float = 0.0) -> List[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM trades WHERE status IN ('CLOSED','CANCELLED') "
                "AND closed_at >= ? ORDER BY closed_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def pnl_summary(self, since: float = 0.0) -> dict:
        """Aggregate stats over trades closed at/after ``since`` (0 == all-time),
        plus live counts of running trades."""
        with self._lock:
            closed = self._db.execute(
                "SELECT outcome, pnl_r, tp1_at, tp2_at, tp3_at, sl_at "
                "FROM trades WHERE status = 'CLOSED' AND closed_at >= ?",
                (since,),
            ).fetchall()
            cancelled = self._db.execute(
                "SELECT COUNT(*) n FROM trades WHERE status = 'CANCELLED' "
                "AND closed_at >= ?",
                (since,),
            ).fetchone()["n"]
            pending = self._db.execute(
                "SELECT COUNT(*) n FROM trades WHERE status = 'PENDING'"
            ).fetchone()["n"]
            active = self._db.execute(
                "SELECT COUNT(*) n FROM trades WHERE status = 'ACTIVE'"
            ).fetchone()["n"]

        wins = sum(1 for r in closed if r["outcome"] == "WIN")
        losses = sum(1 for r in closed if r["outcome"] == "LOSS")
        resolved = wins + losses
        total_r = sum((r["pnl_r"] or 0.0) for r in closed)
        tp1 = sum(1 for r in closed if r["tp1_at"])
        tp2 = sum(1 for r in closed if r["tp2_at"])
        tp3 = sum(1 for r in closed if r["tp3_at"])
        stopped = sum(1 for r in closed if r["sl_at"])
        # break-even stops: stopped out but TP1 had already banked
        be = sum(1 for r in closed if r["sl_at"] and r["tp1_at"])
        return {
            "since": since,
            "wins": wins,
            "losses": losses,
            "resolved": resolved,
            "win_rate": (wins / resolved * 100.0) if resolved else 0.0,
            "total_r": total_r,
            "avg_r": (total_r / len(closed)) if closed else 0.0,
            "closed": len(closed),
            "cancelled": cancelled,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "stopped": stopped,
            "be_stops": be,
            "pending": pending,
            "active": active,
        }

    def close(self) -> None:
        with self._lock:
            self._db.close()
