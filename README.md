# 🎯 Sniper Signals — Institutional Binance Futures Signal Engine

A production-grade Python engine that scans **every USDT-perpetual on Binance
Futures with 24h volume above your threshold** (default 10M) and pushes
**high-conviction, fully-detailed trade setups** to Telegram.

- **Signals only.** No auto-trading — it tells you *what*, *where*, and *why*; you
  pull the trigger.
- **Two independent institutional strategies**, each scored and alerted separately.
- **Multi-timeframe** by design: 4h bias → 1h structure/zone → 15m trigger.
- Every alert ships with: side, grade, entry **zone**, stop-loss, **TP1/TP2/TP3**
  with R:R, the full multi-timeframe read, a confirmation checklist, and the
  invalidation level.
- **Built-in trade tracker.** Every broadcast call is followed automatically —
  entry fill, TP1/TP2/TP3, and stop — and `/pnl` reports your live win rate, R,
  and running trades. (Paper-tracked from the published levels; still no execution.)

> ⚠️ Educational tool. Not financial advice. Trade your own risk.

---

## The strategies

Both are built around how banks & funds actually move price: engineering liquidity
at obvious retail stop clusters, then delivering price to the next pool.

### 1. Liquidity Sweep + Market Structure Shift  *(reversal)*
The stop-hunt playbook. Price spikes **beyond** a prior swing low/high (grabbing
the stops resting there), then snaps back and **changes character** (CHoCH). We
enter the pullback into the **OTE discount/premium zone** (0.62–0.79 fib).

```
Sell-side sweep → bullish CHoCH → long from OTE
Buy-side  sweep → bearish CHoCH → short from OTE
```

### 2. Order Block + Fair Value Gap  *(continuation)*
Trade *with* the institutional trend. In a clean HTF trend we require a **break of
structure** (BOS), locate the **order block** (last opposing candle before the
impulse) — ideally overlapping a **fair value gap** — and enter on the discounted
retrace back into that fresh zone.

```
HTF up + MTF bullish BOS + retrace into fresh OB/FVG → long
HTF down + MTF bearish BOS + retrace into fresh OB/FVG → short
```

Each setup is scored out of 10 across weighted confluences (HTF bias, sweep, CHoCH/BOS,
OTE/discount, FVG, order-block freshness, EMA confluence, volume, RSI, LTF reaction)
and graded **A / B / C**. Only setups at/above your `MIN_SCORE` are pushed, and each
passes strict geometry gates (precise zone width, max risk %, monotonic ≥1R targets).

---

## Trade tracker & `/pnl`

Every signal the bot **broadcasts** is registered as a tracked trade and followed
to completion, so you get an honest, self-updating scoreboard instead of just a
firehose of setups.

**Lifecycle** (checked on closed 1m candles, so a single wick is never missed):

```
PENDING  ──price touches entry zone──▶  ACTIVE
ACTIVE   ──TP1 ─▶ TP2 ─▶ TP3────────▶  CLOSED · WIN
ACTIVE   ──stop───────────────────────▶  CLOSED · WIN if a TP banked, else LOSS
PENDING  ──entry never fills in time──▶  CANCELLED
```

You get a **live Telegram message on every state change** — entry filled, each
TP hit, stop, break-even, expiry.

**Scoring model** (deliberately simple — this is a tracker, not an execution engine):

- Entry is assumed filled at the published entry; the position is three equal
  thirds scaled out at TP1 / TP2 / TP3.
- With `MOVE_SL_TO_BE` (default on) the stop moves to **break-even after TP1**, so
  a trade that reaches TP1 can never turn into a loss.
- **Win** = reached at least TP1 before the stop; **loss** = stopped first.
  Realised **R** is the blended R across the three thirds.

`/pnl` (optionally `/pnl 7d`, `/pnl 30d`, `/pnl all`) reports win rate, net &
average R, TP1/2/3 hit counts, stop-outs (incl. break-even), plus your running
and recently-closed trades with live unrealised R.

---

## Setup

```bash
# 1. install deps
pip install -r requirements.txt

# 2. configure
cp .env.example .env
#    then edit .env:
#    - TELEGRAM_BOT_TOKEN  from @BotFather
#    - TELEGRAM_CHAT_ID    your numeric id (@userinfobot), a group id, or @channel

# 3. (optional) verify the engine end-to-end against live Binance data — no token needed
python selftest.py 60

# 4. run
python run.py
```

The bot posts `✅ Sniper online` on startup and then scans continuously.

---

## Telegram commands

| Command | What it does |
|---|---|
| `/status` | Engine health, symbols tracked, last scan, signals/24h |
| `/scan <symbol>` | Analyse one symbol right now, e.g. `/scan BTC` — shows the read even if no setup |
| `/top` | On-demand scan for the best setups forming this moment |
| `/signals` | Last 10 signals |
| `/pnl [7d\|30d\|all]` | Win rate, net R, TP1/2/3 hits, stop-outs + running & recently-closed trades |
| `/stats` | Totals by strategy and grade |
| `/universe` | Symbols currently tracked + top by volume |
| `/settings` | Current configuration |
| `/setminscore <0-10>` | Tune the minimum confluence score live |
| `/setminvol <usdt>` | Change the 24h volume filter (rebuilds the universe) |
| `/mute` · `/unmute` | Pause / resume alerts |
| `/help` | Command list |

---

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `MIN_QUOTE_VOLUME` | `10000000` | Only scan symbols above this 24h quote volume |
| `SCAN_INTERVAL` | `300` | Seconds between full scans |
| `UNIVERSE_REFRESH` | `1800` | Seconds between volume/universe refreshes |
| `TF_HTF / TF_MTF / TF_LTF` | `4h / 1h / 15m` | Multi-timeframe set |
| `KLINE_LIMIT` | `300` | Candles fetched per timeframe |
| `MIN_SCORE` | `6` | Minimum score (0–10) to alert |
| `SIGNAL_COOLDOWN` | `7200` | Seconds before the same symbol+strategy+side can re-fire |
| `MAX_SIGNALS_PER_CYCLE` | `8` | Safety cap on alerts per scan |
| `TRACK_TRADES` | `true` | Track broadcast signals & enable `/pnl` |
| `TRACK_INTERVAL` | `60` | Seconds between price checks on open trades |
| `TRACK_TF` | `1m` | Candle interval used to detect fills/TP/SL |
| `ENTRY_TIMEOUT` | `43200` | Cancel a signal if its entry never fills within this many seconds |
| `MOVE_SL_TO_BE` | `true` | Move stop to break-even once TP1 is banked |
| `CONCURRENCY` | `12` | Max simultaneous Binance requests |
| `ALLOWED_USER_IDS` | *(empty)* | Restrict command access (blank = everyone) |
| `EXCLUDE_SYMBOLS` | `USDCUSDT,BTCDOMUSDT` | Always skip these |

**Scalping preset:** `TF_HTF=1h TF_MTF=15m TF_LTF=5m SCAN_INTERVAL=120`.
For a very large universe, keep `KLINE_LIMIT` ≤ 300 and `SCAN_INTERVAL` ≥ 120 to stay
comfortably inside Binance rate limits (the client also self-throttles on weight/429).

---

## Architecture

```
run.py                 → entrypoint
selftest.py            → offline integrity test against live data
sniper/
  config.py            → env-driven, live-tunable settings
  client.py            → async Binance Futures REST (throttle + backoff)
  universe.py          → volume-filtered symbol universe + price precision
  candles.py           → NumPy OHLCV container (drops the forming candle)
  indicators.py        → ATR / EMA / RSI / RMA / SMA (no TA dependency)
  structure.py         → swings, trend, BOS / CHoCH
  smc.py               → FVGs, order blocks, liquidity, sweeps, fib/OTE
  signal.py            → Signal model + grading + target ladder
  strategies/
    liquidity_sweep.py → Strategy 1
    ob_fvg.py          → Strategy 2
  scanner.py           → fetch MTF data + run strategies across the universe
  store.py             → SQLite: cooldowns, history, stats, tracked trades
  tracker.py           → trade lifecycle engine (entry/TP/SL/BE) + /pnl monitor
  formatter.py         → Telegram HTML rendering
  bot.py               → commands, background loops (scan + tracker), lifecycle
```

**Robustness:** all data is REST-polled (no fragile websockets for scanning); every
symbol and strategy is exception-isolated so one bad symbol never stops a scan; the
HTTP client honours Binance weight headers and backs off on 429/418/5xx.
