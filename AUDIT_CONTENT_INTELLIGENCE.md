# Content Intelligence Module — Audit

Read-only audit. No code was modified. All file paths are relative to the repo root (`D:\ISI\strategy_harvester`). Line numbers refer to the code as of this audit (2026-07-09).

Ground-truth check on the live database: `SELECT source_type, COUNT(*) FROM strategies GROUP BY source_type` returns **only** `image_input: 4` and `local_video: 2`. YouTube (single-video), Telegram, and X have **never produced a saved Strategy Card** in this database, despite the code existing. The `sources` table has **0 rows**. These facts anchor several verdicts below.

---

## 1. YouTube ingestion (transcripts + Whisper fallback)

**Verdict: EXISTS (fully working code path), but effectively unused in practice.**

- `ingestion/youtube_reader.py:26-61` — `YouTubeReader.read()`: extracts the video id (`:66-81`), tries `youtube-transcript-api` first (`:84-98`), falls back to `yt-dlp` audio download + local Whisper transcription (`:100-146`). Both steps are real, not stubbed.
- Bulk/channel mode is also fully implemented: `fetch_channel_videos()` (`:157-211`, lists a channel's videos via `yt_dlp.extract_info(..., extract_flat=True)`) and `bulk_process_channel()` (`:214-281`, loops videos, extracts, saves).
- Wired into the menu at `main.py:78-96` (submenu) → `action_add_youtube_single` (`:99-116`) and `action_add_youtube_channel` (`:119-145`).
- Despite this, the DB has zero `youtube` cards — either it's never been run successfully end-to-end, or every run found `confidence_score == 0` (see `youtube_reader.py:263`, which only saves `if card and card.confidence_score > 0`).

**2. Scheduling:** Manual only. No cron/workflow references `youtube_reader`. `.github/workflows/scanner.yml` (the only workflow in the repo) runs `python -m scheduler.runner_cron`, which is the price-signal scanner — it never imports anything from `ingestion/`. YouTube ingestion only runs when a human picks menu option 1 in `main.py` (`main.py:47-73`, `:800-818`).

---

## 2. Telegram channel ingestion

**Verdict: PARTIAL — code complete, but credentials are currently missing, so it cannot run at all right now.**

- `ingestion/telegram_reader.py:25-111` — `TelegramReader.read()` uses Telethon to connect with `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` (`:40-48`), normalises the channel name (`:70-77`), and pulls the last N messages async (`:79-111`).
- Checked the live `.env` directly (values not printed, only presence/length): **`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` are all EMPTY.** Calling this reader today raises `IngestionError` immediately at `telegram_reader.py:44-48` before any network call. (Note: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` *are* set, but those belong to the outbound alert bot in `alerts/telegram_alert.py`, a completely separate credential — they do not help this reader.)
- Wired into the menu at `main.py:182-202` (`action_add_telegram`).
- Zero `telegram` cards exist in the DB — consistent with credentials never having been configured.

**2. Scheduling:** Manual only, same as YouTube — invoked exclusively from `main.py:182-202` via menu option 3. No workflow references it.

---

## 3. X/Twitter ingestion

**Verdict: PARTIAL — code complete and credentials now look plausibly real, but the watchlist is empty and it's never produced a saved card.**

- `ingestion/twitter_reader.py` — full pipeline: builds a `tweepy` client from bearer token or 4-key OAuth1 (`_get_client`, `:29-49`), manages a trusted-account list in `config.json` (`add_trusted_account`/`remove_trusted_account`/`list_trusted_accounts`, `:60-109`), fetches recent posts filtered by a like threshold and keyword match (`fetch_account_posts`, `:114-159`), and runs them through the shared extractor (`_extract_from_posts`, `:164-176`; `scrape_single_account`/`scrape_all_trusted_accounts`, `:179-213`).
- `.env` check: `X_BEARER_TOKEN` is set (114 chars — a plausible real bearer-token length, unlike an earlier project note recording a ~25-char placeholder). Cannot confirm live validity without making an actual API call, which this audit does not do.
- **`config.json`'s `"trusted_x_accounts": []` is empty** — `scrape_all_trusted_accounts()` has nothing to scrape (`twitter_reader.py:192-197` short-circuits with `accounts_checked: 0`). Someone has to add accounts via menu option 18 first.
- Zero `twitter` cards in the DB.

**2. Scheduling:** Manual only — `main.py:558-598` (`action_manage_twitter` submenu, options 4/5 trigger scraping). No workflow calls this module.

---

## 4. Screenshot (image) ingestion

**Verdict: EXISTS and is the most-used source (4 of 6 saved cards).**

- `ingestion/image_reader.py` — accepts an image + optional notes (`read_image_input`, `:76-107`), validates format/size (`_normalise_path`/`_encode_image`, `:110-137`), sends the image as base64 plus a vision prompt to Claude Opus 4.8 (`_call_vision`, `:142-172`, model pinned at `:22`), parses the JSON response (`extract_from_image`, `:193-208`), and builds/saves a `StrategyCard` (`build_and_save_card`, `:211-225`).
- Requires `CLAUDE_API_KEY` (checked at `:145-148`) — confirmed **EMPTY** in the live `.env`, so this path is currently non-functional despite having produced cards historically (the 4 `image_input` rows predate the key being unset, or were run in a different environment).
- Wired into the menu at `main.py:522-555` (option 17).

**2. Scheduling:** Manual only — `main.py:522-555`, interactive (`input()` calls for the file path and notes at `image_reader.py:82,97`), incompatible with unattended/scheduled execution as written.

---

## 5. Local video/audio ingestion

**Verdict: EXISTS and has produced cards (2 of 6).**

- `ingestion/media_reader.py:28-66` — `read_local_media()` validates the file (`_normalise_path`, `:69-83`), warns on ffmpeg absence (`:51-54`) and oversize files (`:44-49`), and transcribes locally with Whisper (`_transcribe`, `:86-111`), caching models under `.whisper_models/` (`:25`, `:103-104`).
- Wired into the menu at `main.py:620-650` (option 22).

**2. Scheduling:** Manual only — `main.py:620-650`, requires interactive file-path input (`main.py:624`). No workflow trigger.

---

## 6. Claude-based extraction (raw content → Strategy Card)

**Verdict: EXISTS and fully working, with two independent implementations.**

- **Primary/generic extractor:** `extraction/strategy_extractor.py:56-126` (`extract_strategy`). Two modes:
  - **MANUAL mode** (`_extract_manual`, `:131-163`): prints the prompt for a human to paste into a Claude chat and parses the pasted-back JSON. This is the **default** — `config.json` has `"manual_mode": true`, and `strategy_extractor.py:91-97` explicitly keeps manual mode even when an API key is present, unless `manual_mode` is flipped to `false`.
  - **AUTO mode** (`_extract_auto`, `:168-198`): calls the Claude API directly (`model="claude-haiku-4-5-20251001"`, `:48`). Requires `CLAUDE_API_KEY`, which is currently empty, so AUTO mode cannot run right now even though the code path is complete.
  - Mode selection logic: `:88-99`.
- **Separate vision extractor** for images: `ingestion/image_reader.py:142-172,193-208` (own prompt, own Claude call, model `claude-opus-4-8`). Functionally independent of the text extractor — they don't share a prompt or a call path, only the resulting `StrategyCard` shape.

**2. Scheduling:** N/A as a standalone trigger — it's a function called synchronously by whichever reader ingested content, always inside a manual menu action. No autonomous/scheduled invocation exists anywhere.

---

## 3. WATCHLIST

**Verdict: MISSING (dead schema — a table exists but is never used).**

- A `sources` table is defined in `storage/database.py:141-153`:
  ```sql
  CREATE TABLE IF NOT EXISTS sources (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      source_type TEXT,
      identifier  TEXT,        -- channel handle / url
      label       TEXT,
      active      INTEGER DEFAULT 1,
      date_added  TEXT,
      UNIQUE(source_type, identifier)
  )
  ```
  Helper functions exist: `add_source()` (`:539-551`) and `list_sources()` (`:554-562`).
- **Neither function is called anywhere else in the codebase** (`grep -r "add_source\|list_sources"` across the repo matches only their own definitions in `database.py`). No menu action, no reader, no workflow populates or reads this table. Confirmed empty in the live DB (`SELECT COUNT(*) FROM sources` → `0`).
- **There is no "last checked" checkpoint field at all** — even if the table were wired up, its schema has no timestamp column for tracking per-source progress (no `last_checked_at`, no `last_video_id`, etc.).
- The only thing resembling a watchlist that's actually live is `config.json`'s `"trusted_x_accounts": []` (`twitter_reader.py:60-109`) — a flat list of X usernames with **no per-account checkpoint** either; every scrape re-fetches each account's most recent N posts (`x_posts_to_fetch`, default 20) regardless of what was seen last time (relies entirely on the like-count + keyword filter to narrow results, not a seen-cursor).
- **Conclusion:** the system has no memory of "which YouTube channels/Telegram channels/X accounts am I supposed to be watching, and where did I leave off." Every run is told explicitly what to process (a URL, a channel name, a username typed by a human), except the X trusted-account list, which is a watchlist without a checkpoint.

---

## 4. ASSET SCOPE

**Verdict: Asset-agnostic at the schema/extractor level; ONE source (X ingestion) hardcodes a crypto pre-filter.**

Full text of the primary extraction prompt — `extraction/strategy_extractor.py:25-45`:

```
You are a professional trading strategy analyst.
Read the content below carefully.
Extract any trading strategy you find.
Respond ONLY in valid JSON. No explanation. No markdown.

{
  "name": "",
  "indicators": [],
  "entry_conditions": [],
  "exit_conditions": [],
  "timeframe": "",
  "assets": [],
  "market_condition": "",
  "confidence_score": 0
}

If no strategy found: return confidence_score as 0.
Content:
[CONTENT]
```

Full text of the image/vision extraction prompt — `ingestion/image_reader.py:30-71`:

```
You are a professional trading strategy analyst.
Analyze this image carefully.

This could be:
1. A price chart (look for: candlesticks, indicators,
   trend lines, support/resistance, chart patterns)
2. A social media post about trading
   (look for: strategy descriptions, entry/exit rules,
   indicator settings, trade setups)
3. Both combined

Also consider these user notes:
[USER_TEXT_NOTES]

Extract any trading strategy you can identify.
Be specific about:
- What indicators are visible or mentioned
- What the entry conditions appear to be
- What the exit conditions appear to be
- What timeframe if visible
- What asset if visible or mentioned

Respond ONLY in this JSON format, nothing else:
{
  "image_type": "chart / post / mixed / unknown",
  "strategy_found": true/false,
  "name": "",
  "indicators": [],
  "entry_conditions": [],
  "exit_conditions": [],
  "timeframe": "",
  "assets": [],
  "market_condition": "",
  "confidence_score": 0,
  "visual_observations": "what you saw in the image",
  "notes": "any assumptions or uncertainties"
}

If no clear strategy found: set strategy_found=false
and confidence_score=0.
```

- Neither prompt mentions "crypto," "Bitcoin," or any asset class by name. `assets` is a free-form list (`models/strategy_card.py:50`, `list[str]`) with no enum/validation restricting it to crypto tickers. **Structurally, both prompts could extract a forex or commodities strategy without any code change** — a video about EUR/USD moving averages would parse fine into the same schema.
- **The one real crypto-only constraint** is a client-side keyword pre-filter in the X reader, not the extractor itself — `ingestion/twitter_reader.py:20-24,52-55`:
  ```python
  CRYPTO_KEYWORDS = [
      "btc", "eth", "crypto", "trade", "signal", "buy", "sell", "long", "short",
      "setup", "rsi", "ma", "support", "resistance", "breakout", "strategy",
      "entry", "exit",
  ]
  ```
  used in `_has_keyword()` (`:52-55`) to discard tweets before they ever reach the extractor (`fetch_account_posts`, `:146`). A pure forex-strategy tweet with none of these words (unlikely, since "trade"/"setup"/"entry"/"exit" are generic) would still likely pass, but this list is explicitly crypto-flavored (`btc`, `eth`, `crypto`) and was clearly written with crypto in mind.
- The broader system (backtesting engine, `default_assets`, exchange integration) is crypto/CCXT-only, but that's downstream of ingestion and out of this audit's scope.

---

## 5. STRATEGY CARD SCHEMA

**Verdict: EXISTS — single Pydantic model, `models/strategy_card.py:32-133`.**

Every field currently captured:

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | `str` | auto (`uuid4().hex[:12]`) | `:39`, filled in `model_post_init` (`:86-92`) |
| `name` | `str` | `"Untitled Strategy"` | `:40` |
| `source_type` | `Literal["youtube","telegram","manual","image_input","twitter","local_video","local_audio"]` | `"manual"` | `:18-19,41` |
| `source_url` | `str` | `""` | `:42` |
| `raw_content` | `str` | `""` | `:43` |
| `indicators` | `list[str]` | `[]` | `:45` |
| `entry_conditions` | `list[str]` | `[]` | `:46` |
| `exit_conditions` | `list[str]` | `[]` | `:47` |
| `timeframe` | `str` | `""` | `:49` |
| `assets` | `list[str]` | `[]` | `:50` |
| `market_condition` | `str` (free-form, not the `Literal` alias) | `"any"` | `:52-53` |
| `confidence_score` | `int` (0-100) | `0` | `:55` |
| `status` | `Literal["pending_review","approved","rejected","pending_backtest"]` | `"pending_review"` | `:22,56` |
| `date_added` | `str` (ISO date) | auto (today) | `:57`, `:91-92` |
| `backtest_result` | `Optional[Any]` | `None` | `:58` |
| `approved` | `bool` | `False` | `:59` |
| `entry_rule` | `str` | `""` | Phase 2, parsed boolean expr, `:65` |
| `exit_rule` | `str` | `""` | Phase 2, `:66` |
| `rule_notes` | `str` | `""` | Phase 2, `:67` |
| `version` | `int` | `1` | Phase 3, `:72` |
| `parent_id` | `Optional[str]` | `None` | Phase 3, `:73` |
| `version_notes` | `str` | `""` | Phase 3, `:74` |
| `is_adapted` | `bool` | `False` | Phase 3, `:75` |
| `adaptation_history` | `list[Any]` | `[]` | Phase 3, `:76` |
| `engine` | `str` | `"generic"` | Phase 6 SMC routing, `:82` |
| `engine_signal` | `str` | `""` | Phase 6, `:83` |
| `uses_deviation_filter` | `bool` | `False` | Phase 6, `:84` |

Mirrored 1:1 in the SQLite `strategies` table DDL (`storage/database.py:106-138`), with list/dict fields JSON-encoded as TEXT (`_JSON_FIELDS`, `database.py:80-81`) and booleans as 0/1 integers (`_BOOL_FIELDS`, `:84`).

---

## 6. DEDUPLICATION (same strategy from 5 videos)

**Verdict: MISSING — 5 separate cards would be created, no merge logic exists anywhere.**

- Every extraction path ends in `strategy_store.save_card()` (e.g. `main.py:114-116`, `youtube_reader.py:264`, `twitter_reader.py:174`, `image_reader.py:224`), which calls `db.save(card.to_dict())` (`strategy_store.py:27-31`).
- `db.save()` (`storage/database.py:443-468`) does `INSERT OR REPLACE INTO strategies (...)` keyed by `id`. Since `id` is a fresh random UUID hex generated per card (`utils/helpers.py:78-80`, `uuid.uuid4().hex[:12]`), `INSERT OR REPLACE` never collides — it always inserts a new row.
- Searched the whole repo for any similarity/merge logic (`merge`, `similar_strategy`, `is_duplicate`, `fuzzy`) — the only hits are in `signals/market_scanner.py` and `signals/signal_store.py`, which dedupe live *trading signals* (a completely different subsystem), not Strategy Cards.
- **Conclusion:** if the same strategy idea appears in 5 videos, you get 5 independent `StrategyCard` rows with 5 different ids, each independently backtested, versioned, and (if approved) scanned — no name/content comparison, embedding similarity, or human "these look the same" prompt exists anywhere in the pipeline.

---

## 7. CONTENT DEDUPE (avoiding reprocessing the same video/tweet/message)

**Verdict: PARTIAL — one narrow "seen" check exists (YouTube bulk-channel mode only); every other path reprocesses unconditionally.**

- **The only content-level seen-tracker in the whole ingestion layer:** `ingestion/youtube_reader.py:239-241`, inside `bulk_process_channel()`:
  ```python
  existing = set()
  if skip_existing:
      existing = {c.source_url for c in strategy_store.list_cards()}
  ```
  then at `:249-251`, each video's URL is checked against that set before processing. Gated by `config.json`'s `skip_already_processed` (default `True`, referenced at `youtube_reader.py:231`). This is a **linear re-scan of every existing card's `source_url` on every bulk run** — not an indexed/persisted "seen" table, just an in-memory set rebuilt from the full `strategies` table each time.
- **Single YouTube video** (`main.py:99-116`): no check at all — pasting the same URL twice creates two cards.
- **Telegram** (`telegram_reader.py`, `main.py:182-202`): pulls the last N messages fresh every call, no per-message id tracking, no channel-level "last message id seen" checkpoint. Re-running against the same channel re-fetches and re-extracts the same messages.
- **X/Twitter** (`twitter_reader.py:114-159`): `fetch_account_posts()` always requests the account's most recent tweets; no `since_id` parameter is passed to `client.get_users_tweets()` (confirmed absent from the call at `:129-134`), so it doesn't even use Twitter API's own native pagination-cursor dedup feature. Nothing prevents the same tweet being re-scraped and re-extracted on every scheduled/manual run.
- **Images / local media**: inherently one-shot manual actions (a human picks a specific file each time), so there's no applicable "run" to dedupe against — but also no hash-based check that would catch someone accidentally processing the same screenshot twice.

---

## 8. AUTH/CREDENTIALS

**Verdict: 3 of 6 sources are credential-blocked right now; verified directly against the live `.env` (values not exposed, presence/length only).**

| Source | Required env vars | Status in live `.env` | Can run today? |
|---|---|---|---|
| YouTube (transcript API) | none | N/A | ✅ Yes (no auth needed) |
| YouTube (Whisper fallback) | none (local model) | N/A | ✅ Yes (local, needs ffmpeg on PATH) |
| Telegram channel reader | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` | **All EMPTY** | ❌ No — fails immediately at `telegram_reader.py:44-48` |
| X/Twitter | `X_BEARER_TOKEN` (or 4-key OAuth1 set) | **SET** (bearer, 114 chars — plausible real token, not validated live by this audit) | ⚠️ Credential present, but `trusted_x_accounts` watchlist is empty, so nothing would actually be scraped |
| Screenshot (image) ingestion | `CLAUDE_API_KEY` (vision) | **EMPTY** | ❌ No — blocked at `image_reader.py:145-148` |
| Local video/audio | none (local Whisper) | N/A | ✅ Yes (local, needs ffmpeg on PATH) |
| Text extraction (AUTO mode) | `CLAUDE_API_KEY` | **EMPTY** | ⚠️ Falls back to MANUAL mode (human pastes into a Claude chat) — this still "works" but isn't autonomous |

Separately, and easy to confuse with the above: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` **are** set — but that's the *outbound alert bot* (`alerts/telegram_alert.py`), unrelated to the Telegram *ingestion* reader's Telethon user-account credentials.

---

## Gap list

What's missing to reach the three target states:

**Fully autonomous scheduled runs**
- No workflow/cron triggers any ingestion reader today — `.github/workflows/scanner.yml` only runs the price-signal scanner. A new scheduled job (or a branch in the existing one) would need to be added to invoke YouTube/Telegram/X ingestion.
- Every image and local-media reader is built around blocking `input()` calls (`image_reader.py:82,97`; `main.py:624`) — not callable headlessly without a refactor to accept parameters directly instead of prompting.
- `CLAUDE_API_KEY` is empty, so the default extraction path is MANUAL mode, which itself is interactive (`strategy_extractor.py:131-163`, waits on `input()` for a pasted response) — incompatible with unattended runs even where a reader supports batch mode.
- Telegram ingestion is fully blocked (missing `TELEGRAM_API_ID/HASH/PHONE`); Telethon's first-run auth flow is also interactive by design (`telegram_reader.py:92-95`, needs a login code from the Telegram app) and would need a one-time manual bootstrap even after credentials are added.

**A persisted multi-source watchlist with approval-gated discovery**
- The `sources` table (`database.py:141-153`) exists but is completely disconnected from the rest of the app — needs to actually be populated (add YouTube channels/Telegram channels alongside the existing X account list) and read by each reader instead of requiring a human to type a URL/handle every time.
- No checkpoint field anywhere (`sources` table has none; X scraping doesn't use `since_id`; YouTube bulk mode rebuilds its "seen" set from a full table scan rather than a stored cursor) — needed for "only process what's new since last check."
- No discovery mechanism exists at all (e.g., "here's a candidate channel, approve it before it's added to the watchlist") — today, adding a source to the one working watchlist (`trusted_x_accounts`) is itself the approval step, with no separate discovery/suggestion phase.
- No content-level dedup beyond the one narrow YouTube-bulk `source_url` check — needed so the same video/message/tweet isn't reprocessed (and re-billed against `CLAUDE_API_KEY` usage) on every run.
- No strategy-level merge/similarity detection — five videos describing the same strategy produce five unrelated cards today.

**Asset-agnostic extraction covering crypto + forex + commodities**
- The core extraction prompts (`strategy_extractor.py:25-45`, `image_reader.py:30-71`) are already asset-agnostic and need no change.
- The one real blocker is `twitter_reader.py`'s `CRYPTO_KEYWORDS` pre-filter (`:20-24`) — would need a forex/commodities keyword set (or removing the filter and relying on the extractor's own `confidence_score`) to avoid discarding relevant non-crypto posts before they ever reach extraction.
- `StrategyCard.assets` is already free-form (`list[str]`, no enum), so no schema change is needed there.
- Everything downstream of ingestion (backtesting, exchange data via CCXT, the live scanner) is crypto/CCXT-only — out of scope for this audit, but relevant context if the goal is truly multi-asset-class end-to-end, not just multi-asset-class *ingestion*.
