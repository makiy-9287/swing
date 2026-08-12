"""Telegram bot: command interface + background scan loop + entrypoint."""
from __future__ import annotations

import asyncio
import logging
import sys
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from . import formatter as fmt
from .client import BinanceFutures
from .config import Config
from .scanner import Scanner
from .store import Store
from .tracker import Tracker

log = logging.getLogger("sniper.bot")

HELP = """<b>🎯 Sniper Signals</b> — institutional Binance Futures scanner

Two independent strategies, scanned across every USDT-perp above your volume floor:
• <b>Liquidity Sweep + MSS</b> — stop-hunt reversals
• <b>Order Block + FVG</b> — trend continuation

<b>Commands</b>
/status — engine health &amp; last scan
/scan &lt;symbol&gt; — analyse one symbol now (e.g. /scan BTC)
/top — best setups forming right now
/signals — recent signals
/pnl [7d|30d|all] — win rate, R &amp; live trades
/stats — signal statistics
/universe — symbols currently tracked
/settings — show configuration
/setminscore &lt;0-10&gt; — min confluence score to alert
/setminvol &lt;usdt&gt; — min 24h volume filter
/mute · /unmute — pause / resume alerts
/help — this message

<i>Signals only — no auto-trading.</i>"""


# --------------------------------------------------------------------- access
def _authorized(update: Update, cfg: Config) -> bool:
    if not cfg.allowed_users:
        return True
    user = update.effective_user
    return bool(user and user.id in cfg.allowed_users)


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg: Config = context.bot_data["cfg"]
    if not _authorized(update, cfg):
        if update.message:
            await update.message.reply_text("⛔ Not authorised.")
        return False
    return True


# --------------------------------------------------------------------- commands
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.message.reply_html(HELP)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.message.reply_html(HELP)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    cfg: Config = context.bot_data["cfg"]
    scanner: Scanner = context.bot_data["scanner"]
    store: Store = context.bot_data["store"]
    start_time: float = context.bot_data["start_time"]

    up = int(time.time() - start_time)
    hrs, rem = divmod(up, 3600)
    mins, _ = divmod(rem, 60)
    last = ("never" if not scanner.last_scan_at
            else f"{int(time.time() - scanner.last_scan_at)}s ago")
    today = store.stats()["today"]

    text = (
        "🟢 <b>Sniper online</b>\n\n"
        f"• Uptime: {hrs}h {mins}m\n"
        f"• Symbols tracked: <b>{len(scanner.universe.symbols)}</b>\n"
        f"• Last scan: {last} ({scanner.last_scan_count} symbols, "
        f"{scanner.last_signal_count} signals)\n"
        f"• Signals last 24h: <b>{today}</b>\n"
        f"• Timeframes: {cfg.htf}/{cfg.mtf}/{cfg.ltf}\n"
        f"• Min score: {cfg.min_score}/10 · Cooldown: {cfg.cooldown // 60}m\n"
        f"• Alerts: {'🔇 muted' if cfg.muted else '🔔 live'}"
    )
    await update.message.reply_html(text)


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /scan BTC")
        return
    scanner: Scanner = context.bot_data["scanner"]
    symbol = context.args[0]
    await update.message.reply_text(f"🔎 Analysing {symbol.upper()}…")
    try:
        sigs, ctx = await scanner.diagnose(symbol)
    except Exception as e:
        log.exception("scan command failed: %s", e)
        await update.message.reply_text(f"Error analysing {symbol}: {e}")
        return
    await update.message.reply_html(ctx)
    if sigs:
        for s in sigs:
            await update.message.reply_html(fmt.format_signal(s))
    else:
        await update.message.reply_text("No qualifying setup on either strategy right now.")


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    scanner: Scanner = context.bot_data["scanner"]
    await update.message.reply_text("🛰 Scanning the market for the best setups…")
    try:
        sigs = await scanner.snapshot(limit=5, floor=4)
    except Exception as e:
        log.exception("top command failed: %s", e)
        await update.message.reply_text(f"Scan error: {e}")
        return
    if not sigs:
        await update.message.reply_text("No setups forming right now.")
        return
    for s in sigs:
        await update.message.reply_html(fmt.format_signal(s))


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    store: Store = context.bot_data["store"]
    await update.message.reply_html(fmt.format_recent(store.recent(10)))


def _parse_window(args) -> tuple[float, str]:
    """Turn a /pnl argument into a (since_epoch, label). 0 == all-time."""
    if not args:
        return 0.0, "all-time"
    a = args[0].lower().strip()
    if a in ("all", "alltime", "all-time"):
        return 0.0, "all-time"
    if a in ("today", "24h", "1d", "day"):
        return time.time() - 86400, "last 24h"
    if a in ("week", "7d"):
        return time.time() - 7 * 86400, "last 7d"
    if a in ("month", "30d"):
        return time.time() - 30 * 86400, "last 30d"
    try:
        n = float(a.rstrip("d"))
        if n > 0:
            label = f"last {int(n) if n == int(n) else n}d"
            return time.time() - n * 86400, label
    except ValueError:
        pass
    return 0.0, "all-time"


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    cfg: Config = context.bot_data["cfg"]
    store: Store = context.bot_data["store"]
    if not cfg.track_trades:
        await update.message.reply_text(
            "Trade tracking is disabled. Set TRACK_TRADES=true in .env to enable /pnl."
        )
        return
    since, label = _parse_window(context.args)
    summary = store.pnl_summary(since)
    running = store.open_trades()
    closed = store.recent_closed(limit=8, since=since)
    await update.message.reply_html(fmt.format_pnl(summary, running, closed, label))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    store: Store = context.bot_data["store"]
    await update.message.reply_html(fmt.format_stats(store.stats()))


async def cmd_universe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    scanner: Scanner = context.bot_data["scanner"]
    uni = scanner.universe
    if not uni.symbols:
        await update.message.reply_text("Universe not built yet — try again shortly.")
        return
    top = uni.symbols[:15]
    lines = [f"🌐 <b>{len(uni.symbols)} symbols tracked</b> (24h vol filter)\n"]
    for s in top:
        m = uni.meta[s]
        lines.append(f"• {s} — ${m.quote_volume/1e6:.0f}M")
    if len(uni.symbols) > 15:
        lines.append(f"… and {len(uni.symbols) - 15} more")
    await update.message.reply_html("\n".join(lines))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    cfg: Config = context.bot_data["cfg"]
    d = cfg.as_dict()
    lines = ["⚙️ <b>Settings</b>\n"]
    for k, v in d.items():
        lines.append(f"• {k}: <code>{v}</code>")
    await update.message.reply_html("\n".join(lines))


async def cmd_setminscore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    cfg: Config = context.bot_data["cfg"]
    try:
        n = int(context.args[0])
        assert 0 <= n <= 10
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Usage: /setminscore <0-10>")
        return
    cfg.min_score = n
    await update.message.reply_text(f"✅ Min score set to {n}/10")


async def cmd_setminvol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    cfg: Config = context.bot_data["cfg"]
    scanner: Scanner = context.bot_data["scanner"]
    try:
        v = float(context.args[0])
        assert v >= 0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Usage: /setminvol <usdt>  e.g. /setminvol 25000000")
        return
    cfg.min_quote_volume = v
    scanner.universe.updated_at = 0.0  # force rebuild next scan
    await update.message.reply_text(
        f"✅ Min 24h volume set to ${v/1e6:.1f}M — universe will refresh."
    )


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    context.bot_data["cfg"].muted = True
    await update.message.reply_text("🔇 Alerts muted.")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    context.bot_data["cfg"].muted = False
    await update.message.reply_text("🔔 Alerts live.")


# --------------------------------------------------------------------- loop
async def scanner_loop(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    scanner: Scanner = app.bot_data["scanner"]
    store: Store = app.bot_data["store"]

    while True:
        try:
            signals = await scanner.scan()
            if not cfg.muted:
                for s in signals:
                    signal_id = store.record(s)  # sets cooldown so it won't repeat
                    if cfg.track_trades:
                        store.open_trade(s, signal_id)  # begin tracking this call
                    try:
                        await app.bot.send_message(
                            chat_id=cfg.chat_id,
                            text=fmt.format_signal(s),
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception as e:
                        log.error("Failed to send signal for %s: %s", s.symbol, e)
                    await asyncio.sleep(0.5)  # stay well under Telegram rate limits
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("scan cycle error: %s", e)
        await asyncio.sleep(cfg.scan_interval)


async def tracker_loop(app: Application) -> None:
    """Watch every open trade and push a message on each state change
    (entry filled, TP1/2/3, stop, expiry). DB is updated regardless of mute so
    /pnl stays accurate; the pushes themselves respect the mute switch."""
    cfg: Config = app.bot_data["cfg"]
    tracker: Tracker = app.bot_data["tracker"]

    while True:
        try:
            events = await tracker.poll()
            for ev in events:
                if cfg.muted:
                    continue
                try:
                    await app.bot.send_message(
                        chat_id=cfg.chat_id,
                        text=fmt.format_trade_event(ev),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as e:
                    log.error("Failed to send trade event %s %s: %s",
                              ev.kind, ev.trade.get("symbol"), e)
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("tracker cycle error: %s", e)
        await asyncio.sleep(cfg.track_interval)


# --------------------------------------------------------------------- lifecycle
async def on_startup(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    client = BinanceFutures(concurrency=cfg.concurrency)
    await client.start()
    store = Store()
    scanner = Scanner(client, cfg, store)
    tracker = Tracker(client, cfg, store)

    app.bot_data.update(
        client=client, store=store, scanner=scanner, tracker=tracker,
        start_time=time.time(),
    )

    # build the universe up front so /status is meaningful immediately
    try:
        await scanner.ensure_universe(force=True)
    except Exception as e:
        log.error("Initial universe build failed: %s", e)

    app.bot_data["task"] = asyncio.create_task(scanner_loop(app))
    if cfg.track_trades:
        app.bot_data["track_task"] = asyncio.create_task(tracker_loop(app))

    try:
        await app.bot.send_message(
            chat_id=cfg.chat_id,
            text=(f"✅ <b>Sniper online</b> — tracking "
                  f"{len(scanner.universe.symbols)} symbols.\nSend /help for commands."),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.error("Startup message failed (check TELEGRAM_CHAT_ID): %s", e)


async def on_shutdown(app: Application) -> None:
    for name in ("task", "track_task"):
        task = app.bot_data.get(name)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    client = app.bot_data.get("client")
    if client:
        await client.close()
    store = app.bot_data.get("store")
    if store:
        store.close()


def run() -> None:
    for stream in (sys.stdout, sys.stderr):  # keep Windows consoles emoji-safe
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = Config()
    problems = cfg.validate()
    if problems:
        print("Configuration error(s):")
        for p in problems:
            print("  -", p)
        print("\nCopy .env.example to .env and fill in the values.")
        return

    app = (
        Application.builder()
        .token(cfg.token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("universe", cmd_universe))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("setminscore", cmd_setminscore))
    app.add_handler(CommandHandler("setminvol", cmd_setminvol))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))

    log.info("Starting Sniper Signals bot…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
