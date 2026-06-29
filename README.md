# StrategyHarvester v6.0

An AI-powered **Strategy Intelligence System** for crypto spot trading.
The backend engine is a terminal menu plus a FastAPI layer.

- **Phase 1** — ingests content (YouTube, Telegram, manual notes), extracts
  trading strategies into structured **Strategy Cards**, and stores them in
  SQLite for review and export.
- **Phase 2** — a **backtesting engine**: takes a Strategy Card, pulls ~12
  months of real Binance OHLCV, converts its plain-English rules into testable
  Python (via Claude), simulates trades, and produces a **Backtest Report**
  with a PASS/FAIL verdict saved back to the card.
- **Phase 3** — a **live signal engine + Telegram alerts**: a 24/7 scanner
  watches PASSED strategies across configured coins/timeframes, fires signals
  on closed candles (with market-context + confluence filtering), alerts you on
  Telegram, and tracks each signal's 1H/4H/24H outcome. **Scan-and-alert only —
  it never trades.**
- **Phase 4** — a **learning + adaptation engine**: analyses tracked signal
  outcomes, uses Claude Opus 4.8 to find what separates winners from losers,
  and proposes improved strategy versions. You approve/reject via Telegram
  buttons; approval creates a **new version** (originals are never modified) in
  `pending_backtest` status. Claude is used only for offline analysis — never
  during live scanning.
- **Phase 5** — **image intelligence + X (Twitter) integration**: extract
  strategies from chart/post **screenshots** (Claude Opus 4.8 vision, image +
  notes together), **scrape trusted X accounts** for strategies, and add a
  **live X sentiment** read to signal alerts (adjusting confidence). All three
  degrade gracefully without their API keys and never block the core pipeline.
- **Phase 5.5** — **intelligent + LLM-independent**. The live scanner is now
  **pure Python, zero LLM calls**: rule-based keyword sentiment, a market
  **regime classifier**, a **performance/trust tracker**, and a **parameter
  optimizer** — all from the system's own data. Claude is used only twice:
  extracting new strategies (once) and a **once-per-day** adaptation analysis.
- **Phase 5.6** — **complete content ingestion**: transcribe **local
  video/audio files** with local Whisper (free, offline — covers screen-recorded
  Instagram reels), **bulk-pull an entire YouTube channel** in one go (with
  duplicate-skip), and the existing image reader covers Instagram screenshots.
- **Phase 6** — the **Smart-Money Engine**: a pure-Python, zero-LLM library
  (`smart_money/`) that mechanically detects price-action concepts (swings,
  liquidity sweeps, BOS/MSS, ranges, FVGs, fib zones, order blocks,
  displacement), making ICT/price-action strategies (Range, CRT, Textbook,
  Deviation) backtestable through the Phase 2 engine and live-scannable.

---

## Features (Phase 1)

- **Ingestion** (modular — new sources drop in with zero restructuring):
  - YouTube — single video (transcript via `youtube-transcript-api`, `yt-dlp` +
    Whisper fallback) **or whole-channel bulk pull** (Phase 5.6).
  - Local video/audio files — transcribed locally with Whisper (Phase 5.6).
  - Image/screenshot — chart or social post via Claude vision (Phase 5).
  - Manual notes — pasted text or a `.txt` file.
  - Telegram — last *N* messages from a public channel via Telethon.
- **Extraction** — two modes:
  - **Manual** (default, no API key): prints a prompt to paste into Claude; you paste the JSON back.
  - **Auto** (set `CLAUDE_API_KEY`): calls the Claude API directly.
- **Storage** — SQLite (`strategies`, `sources`, `signals` tables).
- **Review** — approve / reject Strategy Cards.
- **Export** — dump all cards to JSON.
- **API** — FastAPI on `localhost:8000` for the future React frontend.

## Features (Phase 2 — Backtesting)

- **Data** — historical OHLCV from Binance via CCXT, cached locally as CSV.
- **Indicators** — RSI, SMA/EMA (20/50/200), MACD, Bollinger Bands, ADX,
  Volume SMA, Stochastic RSI.
- **Rule parsing** — Claude (`claude-opus-4-8`) converts a card's English
  entry/exit conditions into Python boolean expressions, AST-validated against
  a whitelist; falls back to simple RSI rules if unavailable.
- **Simulation** — one position at a time, $100/trade, 0.1% fee per side.
- **Report** — win rate, avg profit/loss, max drawdown, total return, profit
  factor, Sharpe ratio, full trade log, and a PASS/FAIL verdict
  (PASS when win rate ≥ threshold).

## Features (Phase 3 — Live Signals & Alerts)

- **Market analyzer** — trend (UP/DOWN/SIDEWAYS), strength (via ADX), volume
  confirmation, and a 0–100 market score, so strategies don't fire blindly.
- **Signal detector** — evaluates a strategy's cached rules on the latest
  **closed** candle; entry fired → BUY, exit fired → SELL.
- **Scanner** — every PASSED strategy × coin × timeframe; applies a confidence
  score (base 50 + trend/volume/ADX/timeframe-alignment/confluence bonuses),
  confluence detection, a minimum-confidence gate, and duplicate suppression.
- **Telegram alerts** — signal alerts, daily summary, hourly heartbeat, and
  error notifications (retry x3, never crashes the scanner).
- **Outcome tracker** — fills each signal's 1H/4H/24H % move and a
  WIN/LOSS/NEUTRAL result (foundation for Phase 4 learning).
- **Scheduler** — immediate first scan, then every `scan_interval_minutes`;
  heartbeats, a 00:00 UTC daily summary, error-retry, and clean Ctrl+C shutdown.
- **Versioning** — Strategy Cards carry `version` / `parent_id` /
  `adaptation_history`; strategies are never mutated in place.

## Features (Phase 4 — Learning & Adaptation)

- **Outcome analyzer** — win-rate breakdowns by asset, timeframe, market
  condition, volume confirmation, trend strength, and confidence (read-only).
- **Pattern finder** — Claude Opus 4.8 identifies what separates wins from
  losses and proposes concrete changes (heuristic fallback if no API key).
- **Adaptation engine** — builds an improvement suggestion (only when projected
  gain ≥ `min_improvement_to_suggest`) and sends a Telegram approval request
  with inline **✅ APPROVE / ❌ REJECT / 👁 ORIGINAL** buttons.
- **Version manager** — on approval, creates a new versioned strategy
  (`version+1`, `parent_id`, `is_adapted`, `adaptation_history`) in
  `pending_backtest` status; the original keeps running untouched.
- **Approval listener** — a background thread polls Telegram and applies your
  button presses (or `APPROVE <id>` / `REJECT <id>` text replies).
- **Permanent memory** — every insight and suggestion is stored in SQLite
  (`learning_insights`, `adaptation_suggestions`).
- **Scheduled learning** — runs weekly automatically; reminds you about
  suggestions pending longer than `suggestion_reminder_days`.

## Features (Phase 5 — Image + X Intelligence)

- **Image + text engine** — feed a chart or post screenshot (PNG/JPG/JPEG/
  WEBP/GIF) plus optional notes; Claude Opus 4.8 reads **both together** and
  extracts a Strategy Card (menu 17). Images are base64-encoded; max 5 MB.
- **X strategy scraper** — curate trusted X accounts (menu 18), scrape their
  crypto posts (filtered by likes + keywords), and run them through the same
  extractor. Auto-scrapes every `x_scrape_interval_hours` and notifies on new
  finds.
- **X sentiment engine** — when a signal fires, checks recent sentiment about
  that coin from your trusted accounts, **adjusts the confidence** (±10), and
  adds an `🐦 X Sentiment` section to the Telegram alert. Cached for
  `sentiment_cache_minutes`; **never blocks an alert** if it fails.

## Features (Phase 5.5 — Intelligence Layer, zero-LLM live)

- **Keyword sentiment** — instant, free, rule-based scoring (−100..+100)
  replaces the LLM for live signals. Bullish/bearish/mild word lexicons +
  a 1.5× multiplier when the exact coin is mentioned.
- **Regime classifier** — pure-Python classification into BULL_TRENDING,
  BEAR_TRENDING, BULL_VOLATILE, BEAR_VOLATILE, or SIDEWAYS (trend, ADX, ATR
  volatility, ROC momentum, volume trend). The scanner **skips all strategies**
  for an asset in SIDEWAYS, blocks counter-trend signals, and trims confidence
  in volatile regimes. Saved to `regime_history` each scan.
- **Performance tracker + trust score** — win rates by asset / timeframe /
  market / volume / trend strength, plus a 0–100 **trust score**. The scanner
  only fires a strategy in its historically-best conditions, and alerts show
  `⭐ Trust Score`.
- **Parameter optimizer** — grids RSI/ADX thresholds and MA periods, runs
  mini-backtests (reusing the Phase 2 engine), and saves the best params to
  `parameter_optimizations` — **never auto-applied**.
- **Daily adaptation** — at **01:00 UTC** the system updates performance,
  optimizes parameters, generates suggestions, and Telegrams a **Daily Learning
  Report** with `✅ APPROVE ALL` / `👁 REVIEW ONE BY ONE` buttons.

---

## Project structure

```
strategy_harvester/
├── main.py                    # Terminal menu entry point
├── config.json                # Settings
├── .env                       # Secrets (never commit)
├── requirements.txt
├── ingestion/                 # Content readers (base_reader + youtube/text/telegram)
├── extraction/                # strategy_extractor.py (manual + auto modes)
├── backtesting/               # Phase 2: data_fetcher, indicator_engine,
│                              #          rule_parser, backtest_runner, report_generator
├── signals/                   # Phase 3: market_scanner, signal_detector,
│                              #          market_analyzer, signal_store
├── alerts/                    # Phase 3/4: telegram_alert.py (alerts + approval listener)
├── scheduler/                 # Phase 3/4: runner.py (24/7 loop + weekly learning)
├── learning/                  # Phase 4: outcome_analyzer, pattern_finder,
│                              #          adaptation_engine, version_manager
├── smart_money/               # Phase 6: swing_detector, liquidity_sweep,
│                              #   market_structure, ranges, fvg, fib_zones,
│                              #   order_blocks, displacement, smc_engine
├── sentiment/                 # Phase 5: sentiment_analyzer, sentiment_store
├── ingestion/ (+image_reader, # Phase 5: image + text, X scraper;
│   +twitter_reader,           # Phase 5.6: +media_reader (local video/audio),
│   +media_reader)             #            youtube_reader bulk-channel pull
├── storage/                   # database.py + strategy_store.py
├── models/                    # strategy_card.py + backtest_report.py + signal.py
├── api/                       # FastAPI app + routes (strategies, backtesting,
│                              #          signals, learning)
└── utils/                     # helpers.py
```

---

## Setup

> Requires **Python 3.10+**. For the Whisper/YouTube-audio fallback you also
> need **ffmpeg** installed and on your PATH.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure secrets
#    Copy the template and fill in what you need.
cp .env.example .env        # (or copy manually on Windows)
```

### `.env` keys

| Key                | Needed for                                  |
|--------------------|---------------------------------------------|
| `TELEGRAM_API_ID`  | Telegram reader (get from my.telegram.org)  |
| `TELEGRAM_API_HASH`| Telegram reader                             |
| `TELEGRAM_PHONE`   | Telegram reader (your phone, e.g. +123...)  |
| `CLAUDE_API_KEY`   | Auto extraction, rule parsing, learning, **image reading & sentiment** |
| `BINANCE_API_KEY`  | Higher Binance rate limits (optional)       |
| `BINANCE_API_SECRET`| Higher Binance rate limits (optional)      |
| `X_BEARER_TOKEN`   | X scraping + sentiment (X API v2)           |
| `X_API_KEY` / `X_API_KEY_SECRET` | X API (OAuth1 fallback)       |
| `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | X API (OAuth1 fallback) |

All keys are optional — anything you leave blank simply disables that feature.
Binance **public** OHLCV (used by the backtester) needs no key at all; without
a `CLAUDE_API_KEY`, rule parsing falls back to simple RSI rules.

---

## Running

### Terminal menu

```bash
python main.py
```

```
=== StrategyHarvester v5.6 ===
1.  Add YouTube Content (single video or whole channel)
2.  Add manual notes / text
3.  Add Telegram channel
4.  View all Strategy Cards
5.  Approve / Reject strategies
6.  Run Backtest on Strategy
7.  View Backtest Reports
8.  View PASSED strategies (ready for signals)
9.  Export to JSON
10. Start API server
11. Start Live Signal Engine
12. View Live Signals
13. View Signal History
14. Run Learning Analysis
15. View Strategy Lineage
16. View Adaptation Suggestions
17. Add Image + Text (chart or post screenshot)
18. Manage Twitter/X Accounts
19. View Market Regimes
20. Run Parameter Optimization
21. View Performance Dashboard
22. Add Local Video/Audio File
23. Exit
```

### Cloud deployment (24/7)

To run the scanner + learning engine 24/7 on a free Oracle Cloud VM, see
**[deploy.md](deploy.md)**. In short: `requirements_prod.txt` (lean, pinned
deps), `python -m scheduler.runner_prod` (no-menu production entry with clean
SIGTERM shutdown, auto-restart, a `:8080` health endpoint, and daily-rotated
logs), and `scripts/setup_oracle.sh` to install + register the `stratharv`
systemd service. The engine refreshes its Top-50 watchlist daily at 00:30 UTC.

### API server

From inside the `strategy_harvester/` folder:

```bash
uvicorn api.app:app --reload --port 8000
```

Then open <http://localhost:8000/docs> for interactive API docs.

| Method | Path                          | Description              |
|--------|-------------------------------|--------------------------|
| GET    | `/strategies`                 | List all Strategy Cards  |
| GET    | `/strategies/{id}`            | Get one Strategy Card    |
| POST   | `/strategies/add`             | Add a strategy manually  |
| PATCH  | `/strategies/{id}/approve`    | Approve a strategy       |
| PATCH  | `/strategies/{id}/reject`     | Reject a strategy        |
| POST   | `/backtest/run`               | Run a backtest (body: `strategy_id`, `asset`, `timeframe`) |
| GET    | `/backtest/all`               | All saved backtest reports |
| GET    | `/backtest/passed`            | Only PASSED strategies   |
| GET    | `/backtest/{strategy_id}`     | One strategy's report    |
| GET    | `/signals/active`             | All active signals       |
| GET    | `/signals/today`              | Signals generated today (UTC) |
| GET    | `/signals/history?page=1`     | All signals, paginated (50/page) |
| GET    | `/signals/summary`            | Daily summary stats      |
| GET    | `/signals/{id}/outcome`       | Outcome data for one signal |
| GET    | `/signals/{asset}`            | Signals for an asset (e.g. `/signals/BTC`) |
| GET    | `/learning/insights/{strategy_id}` | Latest learning insight |
| GET    | `/learning/suggestions`       | All adaptation suggestions |
| GET    | `/learning/suggestions/pending` | Only pending suggestions |
| POST   | `/learning/suggestions/{id}/approve` | Approve → create new version |
| POST   | `/learning/suggestions/{id}/reject`  | Reject a suggestion |
| GET    | `/learning/lineage/{strategy_id}` | Full version tree |
| GET    | `/performance/all`            | All strategy performance stats |
| GET    | `/performance/rankings`       | Strategies ranked by trust score |
| GET    | `/performance/{strategy_id}`  | Performance for one strategy |
| GET    | `/regime/{asset}/{timeframe}` | Current market regime (live) |
| GET    | `/regime/history/{asset}`     | Regime history for an asset |
| GET    | `/optimization/{strategy_id}` | Latest optimization result |

---

## Extraction modes explained

- **Manual mode** is the default and needs no API key. The app prints the
  full prompt (with your content embedded). Paste it into a Claude chat, copy
  the JSON reply, paste it back into the terminal, and finish with a line
  containing only `END`.
- **Auto mode** activates when `CLAUDE_API_KEY` is set **and** `manual_mode`
  is `false` in `config.json`. The Claude API is called for you.

---

## Content ingestion (Phase 5.6)

### Local video / audio files (menu 22)

Transcribe any **local** media file with Whisper (local, free, offline) and run
the transcript through the strategy extractor.

- **Formats:** video `.mp4 .mov .mkv .avi .webm`, audio `.mp3 .wav .m4a .aac`.
- **Whisper model:** set `whisper_model` in config (`tiny`/`base`/`small`/
  `medium`; default `base`). Smaller = faster, larger = more accurate. Models
  are cached under `.whisper_models/` on the project drive.
- **Requires `ffmpeg`** on your PATH (Whisper uses it to read media). Install
  from <https://ffmpeg.org/download.html>; the app prints clear instructions if
  it's missing.
- Long files show progress and may take a few minutes.

### Bulk YouTube channel pull (menu 1 → 2)

Feed a channel URL (`youtube.com/@name`, `/c/name`, or `/channel/UC…`) and the
app lists its videos via `yt-dlp`, then processes the latest *N* (you choose
All / a number / the default). It **skips videos already in the DB**
(`skip_already_processed`), waits `delay_between_videos_seconds` between videos,
and prints a running count of strategies found.

### Instagram workflow (no extra build — covered by existing tools)

Instagram has no public API, so use screenshots / screen-recordings:

| Instagram content | How to ingest |
|---|---|
| A post, highlight, or chart screenshot | **Menu 17** (Add Image + Text) — Claude reads the image + your notes |
| A reel with someone **talking** | Screen-record it on your phone, save the video, then **Menu 22** (Add Local Video/Audio) — Whisper transcribes the audio |

Both paths feed the same extract → backtest → signals pipeline.

---

## Smart-Money Engine (Phase 6)

A **pure-Python, zero-LLM** library (`smart_money/`) that gives every price-action
concept a **mechanical definition** (all thresholds in config.json). It routes a
strategy card's signals when `engine="smc"` (with `engine_signal` in
`range`/`crt`/`textbook`/`filter`); generic indicator strategies keep the Phase 2
path. It runs identically in **backtesting and live scanning**.

### Mechanical definitions

| Module | Concept (mechanical) |
|---|---|
| `swing_detector` | **Swing high/low** = high/low strictly beyond N candles each side (`swing_lookback`). Structure = HH/LH/HL/LL sequence. |
| `liquidity_sweep` | **Sweep** = wick crosses a level by ≥ `sweep_min_wick_pct` but the **body closes back** on the original side. |
| `market_structure` | **BOS** = with-trend close beyond the last swing. **MSS/CHOCH** = first counter-trend close-through (e.g. close above the last lower-high in a downtrend). |
| `ranges` | **Range** = over `range_lookback`, height ≤ `range_max_height_pct` and ≥2 touches each edge → high/mid/low. |
| `fvg` | **FVG** = 3-candle imbalance (`c1.high < c3.low` bull / `c1.low > c3.high` bear), gap ≥ `fvg_min_gap_pct`. |
| `fib_zones` | Retracement levels; **discount zone** = `discount_zone` band (0.705–0.786) for longs. |
| `order_blocks` | Last opposite-colour candle before a structure break defines the OB zone (`ob_lookback`). |
| `displacement` | Body > `displacement_body_mult` × avg body of last `displacement_lookback`, **and** it creates an FVG. |

### Strategy signals (`smc_engine`)

- **Range** — sweep range_low + close back inside → LONG, target range_high.
- **CRT** — C2 sweeps C1's low + closes back inside C1's range → LONG, target C1
  high; flags confluence with unfilled FVG / order block.
- **Textbook A+** — downtrend → MSS-up → retrace into the discount zone → LONG,
  target = the broken high, stop below the swept low (strictest, fewest signals).
- **Deviation filter** — `deviation_probability()` returns HIGH if the latest
  up-move broke a prior higher-high, else LOW; the scanner downgrades LONG
  confidence (−15) on LOW when `uses_deviation_filter` is set.

The mentor (khaanhassan000) cards are tagged `engine="smc"` and backtest through
the **same** Phase 2 sizing/fees/metrics/verdict. These are raw mechanical
implementations — they generate signals and compute metrics, but the thresholds
(in config) are meant to be **tuned**; out-of-the-box win rates are a starting
point, not a finished edge.

---

## Backtesting (Phase 2)

Run a backtest from the menu (option 6) or `POST /backtest/run`. The pipeline:

1. **Fetch** ~12 months of OHLCV for the asset/timeframe from Binance (cached
   under `data/cache/` as CSV, so repeat runs are instant and offline).
2. **Indicators** — the full canonical set is computed and added to the data.
3. **Rules** — the card's English entry/exit conditions are converted to Python
   boolean expressions by Claude (`claude-opus-4-8`), validated, and cached on
   the card. No key / invalid output → safe fallback (`RSI<30` in, `RSI>70` out).
4. **Simulate** — scan candles forward; enter when the entry rule is true and
   flat, exit when the exit rule is true. One position at a time, **$100/trade**,
   **0.1% fee per side**.
5. **Report** — metrics + PASS/FAIL verdict, printed as a box and saved to the
   card's `backtest_result`.

> **`pandas-ta` note:** the indicator engine ships self-contained pandas/numpy
> implementations because `pandas-ta` cannot install on Python 3.14 (its
> `numba` dependency caps at <3.14). On an older Python where `pandas-ta` is
> present it is detected, but the same canonical column names are always used.

> **Rule safety:** generated expressions are checked with `ast.parse` against a
> strict whitelist (only `df`/`prev`, known indicator columns, comparisons and
> boolean/arithmetic operators). Function calls, imports, and unknown names are
> rejected, so the expressions can be evaluated safely.

---

## Live Signal Engine (Phase 3)

### Telegram bot setup

1. Open Telegram → search **@BotFather**.
2. Send `/newbot` → follow the steps → copy the **TOKEN**.
3. Start a chat with your new bot and **send it any message first**
   (a bot cannot message you until you've messaged it).
4. Visit in a browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find `"chat":{"id": YOUR_CHAT_ID}` in the response — that number is **your
   personal chat id** (not the bot's id).
6. Add both to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```
7. Test it: menu option **11 → "Send a Telegram test message first"** before
   starting the engine.

> ⚠️ The chat id is **not** the same as the numeric part of the bot token. If
> you set it to the bot's own id, Telegram replies
> *"the bot can't send messages to the bot"*.

### Starting & stopping the engine

- **Start:** menu option **11** (or call `scheduler.runner.start_scheduler()`).
  It runs the first scan immediately, then loops every `scan_interval_minutes`.
- **Stop:** press **Ctrl+C** — it prints `🛑 StrategyHarvester stopped cleanly`
  and sends a Telegram shutdown notice.

Each scan cycle runs: `expire_old_signals()` → `run_scan()` →
`update_signal_outcomes()`. A heartbeat is sent every
`heartbeat_interval_minutes`, and a daily summary at `daily_summary_time_utc`.

### Alert types

| Alert | When | Meaning |
|-------|------|---------|
| 🟢/🔴 **Signal** | a strategy fires on a closed candle | a BUY/SELL setup with entry zone, market context, confidence, and any confluence |
| 📊 **Daily summary** | 00:00 UTC | counts, top asset/strategy, avg confidence for the day |
| 💓 **Heartbeat** | every 60 min | proof the engine is alive |
| ⚠️ **Error** | a scan throws | the scanner hit an error and will retry in 5 min |

All signals are **informational only** — StrategyHarvester never places trades.
All timestamps are **UTC**.

---

## Learning & Adaptation (Phase 4)

The learning engine turns tracked signal outcomes into better strategies —
**always with your approval, and never by modifying the original.**

### How it works

1. **Analyse** — `outcome_analyzer` loads a strategy's signals that have
   outcomes and computes win-rate breakdowns (asset, timeframe, market
   condition, volume, trend strength, confidence). Needs ≥
   `min_signals_for_analysis` decided outcomes (default 10).
2. **Find patterns** — `pattern_finder` sends the breakdown to **Claude Opus
   4.8**, which returns key findings + concrete suggested changes. (No API key →
   a deterministic heuristic is used instead.) The result is saved to
   `learning_insights`.
3. **Suggest** — `adaptation_engine` builds an improvement suggestion (only if
   the projected gain ≥ `min_improvement_to_suggest`), saves it to
   `adaptation_suggestions` as **PENDING**, and sends a Telegram approval
   request with inline buttons.
4. **Decide** — you press a button (or reply with text):
   - **✅ APPROVE** → `version_manager` creates a **new version** of the
     strategy with the changes applied: `version+1`, `parent_id` set,
     `is_adapted=true`, status **`pending_backtest`**, `approved=false`,
     `backtest_result` cleared.
   - **❌ REJECT** → the suggestion is archived; the original is untouched.
   - **👁 ORIGINAL** → the original strategy's details are sent to Telegram.

### Approving / rejecting via Telegram

- Tap **✅ APPROVE** / **❌ REJECT** on the message, **or**
- Reply with `APPROVE <suggestion_id>` or `REJECT <suggestion_id>`.

A background **approval listener** (started with the signal engine, menu 11)
polls Telegram every `approval_poll_interval_seconds` and applies your choice.

### Strategy versioning & lineage

- Strategies form a **family tree**: each adapted version points to its
  `parent_id`. Originals are read-only and keep running.
- A new version is **not live** until it passes a **fresh backtest** (menu 6)
  and is approved — `pending_backtest` until then.
- View the tree with menu **15** or `GET /learning/lineage/{id}`:
  ```json
  { "original": {...}, "versions": [ {...} ],
    "total_versions": 2, "best_performing_version": "v2 (win rate 64.5%)" }
  ```

### Running it

- **Manual:** menu **14 → Run Learning Analysis** (pick a strategy, see findings,
  optionally send to Telegram for approval).
- **Automatic:** the scheduler runs learning **weekly**
  (`learning_run_interval_days`) for every PASS strategy with enough outcomes,
  and reminds you about suggestions pending longer than
  `suggestion_reminder_days`.

> **Claude is never in the live path.** Scanning/alerting stays pure Python;
> Claude Opus 4.8 is used only for offline analysis and suggestion. The SQLite
> DB is the permanent memory.

---

## Image + X Intelligence (Phase 5)

### Add a chart/post image (menu 17)

1. Choose **17. Add Image + Text**.
2. Paste the image path (drag-drop works; quotes/backslashes are handled).
3. Optionally add notes (source, strategy hints, context).
4. Claude Opus 4.8 reads the **image and notes together** and extracts a
   Strategy Card; you're shown what it observed and can send it to backtest.

- Supported formats: **PNG, JPG, JPEG, WEBP, GIF**; max **5 MB**.
- Requires `CLAUDE_API_KEY` (vision). Saved with `source_type = "image_input"`.

### Manage X accounts (menu 18)

A submenu to **add / remove / view** trusted accounts, **scrape all**, or
**scrape a single** account. Accounts are stored in `config.json`
(`trusted_x_accounts`) — never hardcoded. Scraping fetches recent posts,
filters by `x_min_likes` + crypto keywords, and runs them through the extractor.
The scheduler also auto-scrapes every `x_scrape_interval_hours` and Telegrams a
summary of new strategies.

### X sentiment on alerts

When a signal fires, the scanner checks recent sentiment about that coin from
your trusted accounts (Claude Opus 4.8), adjusts the confidence, and adds a
section to the alert:

```
🎯 Confidence : 90/100 (adjusted from 80)

🐦 X Sentiment: 🟢 BULLISH (+10)
   @CryptoTester → 🟢 Bullish
   Score: +65/100
```

Confidence adjustment: **BULLISH+BUY / BEARISH+SELL → +10**;
**BEARISH+BUY / BULLISH+SELL → −10**; NEUTRAL/MIXED → 0. Sentiment is cached
for `sentiment_cache_minutes`, can be turned off with `sentiment_enabled`, and
**never blocks a signal alert** — if there are no trusted accounts or the X/
Claude call fails, the alert is sent without it.

> Requires valid **X API** credentials and `CLAUDE_API_KEY`. Without them,
> these features are skipped silently and the rest of the system runs normally.

---

## Intelligence layer (Phase 5.5) — how it learns

**Core principle:** the LLM (Claude Opus 4.8) is used only to (1) extract a
strategy from new content, once, and (2) run one **daily** adaptation analysis.
Everything else — scanning, sentiment, regime, trust, optimization — is **pure
Python + the SQLite DB**, which is the system's permanent memory.

- **Keyword sentiment** (`sentiment/keyword_sentiment.py`) — scores posts with a
  weighted lexicon (strong/normal/mild bullish & bearish) and a 1.5× coin
  multiplier; `>30` BULLISH, `<−30` BEARISH, else NEUTRAL (MIXED when both
  camps appear). Used live; the Claude sentiment analyzer remains only as a
  deep fallback.
- **Regime classifier** (`signals/regime_classifier.py`) — combines trend
  (price vs EMA200), strength (ADX), volatility (ATR/price), momentum (ROC), and
  volume trend into one of five regimes. `is_regime_suitable()` gates signals.
- **Performance tracker** (`learning/performance_tracker.py`) — recomputes win
  rates per category and a trust score:
  base win-rate (≤40) + volume bonus (≤15) + trend bonus (≤15) + sample bonus
  (≤20) + consistency bonus (+10), capped at 100. `get_best_conditions()` tells
  the scanner where a strategy historically works.
- **Parameter optimizer** (`learning/parameter_optimizer.py`) — detects the
  tunable parameters in a strategy's rule, grids candidate values, mini-backtests
  each (reusing the Phase 2 runner), and records the best — never auto-applied.
- **Daily adaptation** (01:00 UTC) — performance → optimization → suggestions →
  a Telegram **Daily Learning Report**. Tap **✅ APPROVE ALL** to version every
  suggestion at once (each still needs a fresh backtest), or **👁 REVIEW ONE BY
  ONE** for the individual approve/reject flow. Run it manually from menu 20/21
  or via the dashboard (menu 21). Trigger nothing is auto-applied or
  auto-approved.

The daily scheduler now runs: **00:00** signal summary → **01:00** learning +
adaptation; **every scan** does regime classification + keyword sentiment (no
LLM); **every 6 h** X scraping; **every 60 min** heartbeat.

---

## Configuration (`config.json`)

```json
{
  "default_assets": ["BTC", "ETH", "SOL", "BNB"],
  "default_timeframes": ["1H", "4H", "1D"],
  "manual_mode": true,
  "messages_to_fetch": 50,
  "min_confidence_score": 60,

  "backtest_months": 12,
  "trade_size_usd": 100,
  "win_rate_threshold": 50,
  "binance_fee_percent": 0.1,
  "cache_data_locally": true,
  "data_cache_folder": "data/cache",

  "scan_interval_minutes": 15,
  "min_confidence_to_alert": 60,
  "entry_zone_buffer_percent": 0.2,
  "signal_expiry_candles": 3,
  "volume_confirmation_multiplier": 1.5,
  "adx_strong_trend_threshold": 25,
  "adx_weak_trend_threshold": 15,
  "daily_summary_time_utc": "00:00",
  "heartbeat_interval_minutes": 60,
  "outcome_win_threshold_pct": 1.0,
  "outcome_loss_threshold_pct": -1.0,
  "max_signals_per_scan": 10,

  "min_signals_for_analysis": 10,
  "suggestion_reminder_days": 3,
  "min_improvement_to_suggest": 3.0,
  "approval_poll_interval_seconds": 5,
  "max_versions_per_strategy": 10,

  "adaptation_schedule": "daily",
  "adaptation_time_utc": "01:00",
  "optimization_enabled": true,
  "min_signals_for_optimization": 5,
  "regime_history_enabled": true,

  "trusted_x_accounts": [],
  "x_posts_to_fetch": 20,
  "x_min_likes": 10,
  "x_scrape_interval_hours": 6,
  "sentiment_cache_minutes": 60,
  "sentiment_enabled": true,
  "image_max_size_mb": 5,
  "image_supported_formats": ["png", "jpg", "jpeg", "webp", "gif"],

  "whisper_model": "base",
  "max_video_size_mb": 500,
  "bulk_channel_default_limit": 20,
  "delay_between_videos_seconds": 2,
  "skip_already_processed": true,

  "swing_lookback": 2,
  "sweep_min_wick_pct": 0.05,
  "range_lookback": 40,
  "range_max_height_pct": 12,
  "discount_zone": [0.705, 0.786],
  "displacement_body_mult": 2.0,
  "displacement_lookback": 20,
  "fvg_min_gap_pct": 0.03,
  "ob_lookback": 30
}
```

---

## Roadmap

- **Frontend** — React (Next.js) dashboard; PostgreSQL upgrade.
- **Indicator snapshots on signals** — so the learning engine can analyse exact
  indicator values (e.g. RSI at entry) per outcome, not just market context.
- **Phase 5** — signal generation (the `signals` table/endpoint are stubbed now).
- More ingestion sources (Twitter, Instagram, RSS) via the `BaseReader` contract.

---

## Notes

- Secrets live only in `.env`, which is git-ignored. Never commit it.
- The SQLite DB (`strategy_harvester.db`) and Telegram `.session` files are
  also git-ignored.
- All errors are handled gracefully — the terminal menu never crashes on a
  single failed action.
```
