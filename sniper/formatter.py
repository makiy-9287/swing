"""Render Signal / stats / trade objects into Telegram-ready HTML."""
from __future__ import annotations

import html
import time
from typing import List

from .signal import Signal
from .tracker import unrealized_r

GRADE_STARS = {"A": "⭐⭐⭐", "B": "⭐⭐", "C": "⭐"}


def _esc(text: str) -> str:
    return html.escape(str(text))


def _fmt(value: float, prec: int) -> str:
    return f"{value:.{prec}f}"


def format_signal(s: Signal) -> str:
    p = s.price_precision
    arrow = "🟢 LONG" if s.side == "LONG" else "🔴 SHORT"
    stars = GRADE_STARS.get(s.grade, "")

    lines: List[str] = []
    lines.append(f"{arrow}  <b>{_esc(s.symbol)}</b>")
    lines.append(f"<i>{_esc(s.strategy)}</i>")
    lines.append(f"Grade <b>{s.grade}</b> ({s.score}/{s.max_score}) {stars}")
    lines.append("")

    lines.append("📊 <b>Multi-timeframe</b>")
    lines.append(f"• HTF: {_esc(s.htf)}")
    lines.append(f"• MTF: {_esc(s.mtf)}")
    lines.append(f"• LTF: {_esc(s.ltf)}")
    lines.append("")

    lines.append("🎯 <b>Trade plan</b>")
    lines.append(f"• Entry zone: <code>{_fmt(s.entry_low, p)} – {_fmt(s.entry_high, p)}</code>")
    lines.append(f"• Stop loss: <code>{_fmt(s.sl, p)}</code>  ({s.risk_pct:.2f}% risk)")
    for i, (tp, rr) in enumerate(zip(s.tps, s.rr), start=1):
        lines.append(f"• TP{i}: <code>{_fmt(tp, p)}</code>  ({rr:.2f}R)")
    lines.append("")

    lines.append("✅ <b>Confirmations</b>")
    for conf in s.confirmations:
        lines.append(f"• {_esc(conf)}")
    lines.append("")

    lines.append(f"⚠️ <i>Invalidation:</i> {_esc(s.invalidation)}")
    ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(s.created_at))
    lines.append(f"⏱ {ts} UTC ·")
    return "\n".join(lines)


def format_stats(stats: dict) -> str:
    lines = ["📈 <b>Signal statistics</b>", ""]
    lines.append(f"• Total signals: <b>{stats['total']}</b>")
    lines.append(f"• Last 24h: <b>{stats['today']}</b>")
    lines.append("")
    lines.append("<b>By strategy</b>")
    if stats["by_strategy"]:
        for k, v in stats["by_strategy"].items():
            lines.append(f"• {_esc(k)}: {v}")
    else:
        lines.append("• (none yet)")
    lines.append("")
    lines.append("<b>By grade</b>")
    if stats["by_grade"]:
        for k in ("A", "B", "C"):
            if k in stats["by_grade"]:
                lines.append(f"• {k}: {stats['by_grade'][k]}")
    else:
        lines.append("• (none yet)")
    return "\n".join(lines)


def format_recent(rows) -> str:
    if not rows:
        return "No signals recorded yet."
    lines = ["🗒 <b>Recent signals</b>", ""]
    for r in rows:
        ts = time.strftime("%m-%d %H:%M", time.gmtime(r["ts"]))
        side = "🟢" if r["side"] == "LONG" else "🔴"
        lines.append(
            f"{side} <b>{_esc(r['symbol'])}</b> {r['side']} · "
            f"{_esc(r['strategy_key'])} · {r['grade']} ({r['score']}) · {ts}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------- trade tracker
def _dot(side: str) -> str:
    return "🟢" if side == "LONG" else "🔴"


def _exit_label(t: dict) -> str:
    """How a closed trade finished."""
    if t.get("status") == "CANCELLED":
        return "expired"
    if t.get("tp3_at"):
        return "TP3"
    if t.get("sl_at"):
        return "BE" if t.get("tp1_at") else "SL"
    if t.get("tp2_at"):
        return "TP2"
    if t.get("tp1_at"):
        return "TP1"
    return "—"


def format_trade_event(ev) -> str:
    """One Telegram message announcing a trade state change."""
    t = ev.trade
    p = t["precision"]
    sym = _esc(t["symbol"])
    side = t["side"]
    tag = f"{_dot(side)} <b>{sym}</b> {side}"

    if ev.kind == "ENTRY":
        return (
            f"🎯 <b>ENTRY FILLED</b> · {tag}\n"
            f"<i>{_esc(t['strategy_key'])}</i> — trade is now running.\n"
            f"SL <code>{_fmt(t['sl'], p)}</code> · "
            f"TP1 <code>{_fmt(t['tp1'], p)}</code> · "
            f"TP2 <code>{_fmt(t['tp2'], p)}</code> · "
            f"TP3 <code>{_fmt(t['tp3'], p)}</code>"
        )

    if ev.kind in ("TP1", "TP2", "TP3"):
        n = int(ev.kind[-1])
        rr = t[f"rr{n}"]
        price = t[f"tp{n}"]
        if n == 3:
            return (
                f"🏁 <b>TP3 HIT — full target</b> · {tag}\n"
                f"Price <code>{_fmt(price, p)}</code> · runner closed.\n"
                f"✅ <b>WIN</b> · blended <b>{t['pnl_r']:+.2f}R</b>"
            )
        be = "  ·  SL → break-even" if n == 1 else ""
        return (
            f"✅ <b>TP{n} HIT</b> · {tag}\n"
            f"Price <code>{_fmt(price, p)}</code> (+{rr:.2f}R) · "
            f"banked {n}/3{be}"
        )

    if ev.kind == "SL":
        won = t.get("outcome") == "WIN"
        if won:  # stopped at break-even after banking TP(s)
            return (
                f"🛡 <b>STOPPED — break-even</b> · {tag}\n"
                f"Price returned to <code>{_fmt(t['entry_ref'], p)}</code> after TP1.\n"
                f"Net <b>{t['pnl_r']:+.2f}R</b> (a TP was already banked)."
            )
        return (
            f"🛑 <b>STOPPED OUT</b> · {tag}\n"
            f"SL <code>{_fmt(t['sl'], p)}</code> hit · "
            f"❌ <b>LOSS {t['pnl_r']:+.2f}R</b>"
        )

    if ev.kind == "EXPIRE":
        return (
            f"⌛ <b>ENTRY EXPIRED</b> · {tag}\n"
            f"Price never reached the entry zone in time — signal cancelled."
        )
    return f"{tag} · {_esc(ev.kind)}"


def _running_line(t: dict) -> str:
    tag = f"{_dot(t['side'])} <b>{_esc(t['symbol'])}</b> {t['side']}"
    if t["status"] == "PENDING":
        return f"{tag} · ⏳ awaiting entry"
    banked = sum(1 for k in ("tp1_at", "tp2_at", "tp3_at") if t.get(k))
    ur = unrealized_r(t)
    ur_s = f"{ur:+.2f}R" if ur is not None else "—"
    tp_s = f" · TP{banked}✅" if banked else ""
    return f"{tag} · running{tp_s} · <b>{ur_s}</b>"


def _closed_line(t: dict) -> str:
    tag = f"{_dot(t['side'])} <b>{_esc(t['symbol'])}</b> {t['side']}"
    if t.get("status") == "CANCELLED":
        return f"⌛ {tag} · expired"
    outcome = t.get("outcome") or "—"
    mark = "✅" if outcome == "WIN" else "❌"
    pnl = t.get("pnl_r")
    pnl_s = f"{pnl:+.2f}R" if pnl is not None else "—"
    return f"{mark} {tag} · {outcome} {pnl_s} · {_exit_label(t)}"


def format_pnl(summary: dict, running: List[dict], closed: List[dict],
               window_label: str = "all-time") -> str:
    s = summary
    lines = [f"📊 <b>PnL &amp; Trade Tracker</b> — {window_label}", ""]

    if s["resolved"] == 0 and s["cancelled"] == 0 and not running:
        lines.append("No trades tracked yet. Signals are tracked automatically "
                     "as they're broadcast.")
        return "\n".join(lines)

    net = s["total_r"]
    lines.append(f"Resolved: <b>{s['resolved']}</b>  (✅ {s['wins']} / ❌ {s['losses']})")
    lines.append(f"Win rate: <b>{s['win_rate']:.1f}%</b>")
    lines.append(f"Net: <b>{net:+.2f}R</b> · Avg: <b>{s['avg_r']:+.2f}R</b>/trade")
    lines.append("")
    lines.append(f"🎯 Targets: TP1 <b>{s['tp1']}</b> · TP2 <b>{s['tp2']}</b> · "
                 f"TP3 <b>{s['tp3']}</b>")
    lines.append(f"🛑 Stopped: <b>{s['stopped']}</b> (BE {s['be_stops']}) · "
                 f"⌛ Expired: <b>{s['cancelled']}</b>")

    if running:
        lines.append("")
        lines.append(f"▶️ <b>Running ({len(running)})</b>")
        for t in running[:12]:
            lines.append(_running_line(t))
        if len(running) > 12:
            lines.append(f"… and {len(running) - 12} more")

    if closed:
        lines.append("")
        lines.append("🏁 <b>Recently closed</b>")
        for t in closed[:8]:
            lines.append(_closed_line(t))

    lines.append("")
    lines.append("<i>Win = reached TP1 before stop ·</i>")
    return "\n".join(lines)
