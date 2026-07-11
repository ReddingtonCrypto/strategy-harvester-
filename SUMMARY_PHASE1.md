# Phase 1: Autonomous Watchlist System — Summary

Builds on [AUDIT_CONTENT_INTELLIGENCE.md](AUDIT_CONTENT_INTELLIGENCE.md). X/Twitter was left completely untouched, as scoped (`ingestion/twitter_reader.py` and `config.json`'s `trusted_x_accounts` have zero changes — verified via `git diff --stat`).

## What changed

**1. Schema** (`storage/database.py`)
- `sources` table gained `last_checked_at` (ISO timestamp) and `last_item_id` (text — a video id or Telegram message id) columns.
- Migrated via the existing `_ensure_columns()` helper, so it's a safe, idempotent `ALTER TABLE ADD COLUMN` — **tested against the live database**: ran cleanly, auto-backed-up first (existing `backup_db()` behavior), integrity check passed, all 6 existing strategy cards untouched.
- Added `update_source_checkpoint()` and extended `list_sources()` with `source_type`/`active_only` filters.

**2. CLI source management** (`main.py`)
- `python main.py add-source --type youtube --identifier "<url>" --label "<name>"` and `python main.py list-sources`. `--type` only accepts `youtube`/`telegram` — `twitter` is rejected by argparse (tested).
- `python main.py` with no arguments still launches the interactive menu, completely unchanged (verified: still 23 menu actions, same numbering).
- No new interactive menu option was added (the task only required not breaking an existing one, and none existed for `sources`) — source management is CLI-only for now.

**3. Watchlist-driven readers**
- `ingestion/youtube_reader.py`: new `process_watchlist()`. Reads active YouTube sources, diffs each channel's video list against the stored `last_item_id`, processes only what's new (bootstrapped to the most recent `bulk_channel_default_limit` videos on a source's first run, never a full backfill), advances the checkpoint, and never lets one video or one source's failure stop the rest. **Tested** with a mocked video list: checkpoint correctly advances to the newest id, and a second run correctly finds zero new videos.
- `ingestion/telegram_reader.py`: new `process_watchlist()`. Same pattern, using Telethon's `min_id` for the sequential-message-id checkpoint. **Tested**: gracefully skips (logs a clear reason, returns instead of raising) when `TELEGRAM_API_ID`/`HASH`/`PHONE` are missing — confirmed against your actual `.env`, which currently has all three empty.
- Neither new function calls `input()` — verified by reading the code and by the tests above completing without blocking.

**4. Headless-safe Telegram auth**
- The existing interactive path (`TelegramReader.read()`, used by menu option 3) is untouched and still uses `client.start(phone=...)`, which can prompt for a login code — fine, because that's still only ever invoked interactively.
- The new `process_watchlist()` path never calls `.start()`. It only uses an already-authorized session: either the local session file (if you've logged in interactively before) or a new `TELEGRAM_SESSION_STRING` env var (a portable Telethon `StringSession`) for headless/CI use. If the session isn't authorized, it logs a clear message and returns instead of hanging on a login prompt with no terminal attached.

**5. SUBSCRIPTION extraction mode** (`extraction/strategy_extractor.py`)
- New third mode alongside MANUAL and AUTO: shells out to the local `claude` CLI (`claude -p "<prompt>" --output-format json --model claude-haiku-4-5-20251001`), reads the `"result"` field of the JSON envelope, and parses the strategy JSON from it the same way the other two modes do.
- `CLAUDE_API_KEY`/`ANTHROPIC_API_KEY` are explicitly stripped from the subprocess's environment so this mode can never silently fall back to a billed API key.
- Mode selection is now: explicit `force_mode` argument → `config.json`'s `"extraction_mode"` key → legacy fallback (the original API-key + `manual_mode` boolean check, kept for safety). **Tested** the full priority order with unit checks — all cases resolved correctly.
- `config.json` now has `"extraction_mode": "subscription"` — **this is a global default change**, not scoped only to the new watchlist pipeline. It affects every existing extraction call (single-video YouTube, manual text, interactive Telegram, X scraping) — they'll now try the `claude` CLI instead of the old copy-paste-into-a-chat MANUAL flow. See "assumptions to double check" below.

**6. New scheduled workflow**
- `scheduler/content_intelligence_cron.py`: headless entry point, runs YouTube then Telegram watchlists once and exits. **Tested end-to-end locally** — runs cleanly, exits 0, correctly reports 0 sources/0 strategies with the current empty watchlist and missing Telegram creds.
- `.github/workflows/content_intelligence.yml`: new workflow, `workflow_dispatch` + cron `20 */4 * * *` (every 4 hours, offset from `scanner.yml`'s `:07`/`:37`). Installs Python deps from a new `requirements_content_intel.txt` (deliberately excludes Whisper/torch — see below) plus the Claude Code CLI via npm. **Shares `scanner.yml`'s concurrency group on purpose**, since both workflows commit to the same binary `strategy_harvester.db` file and a rebase can't auto-merge a binary conflict — sharing the group means they can never run concurrently and race on the DB.

## Manual setup steps you still need to do

1. **Add YouTube channels to the watchlist.** Nothing is watched yet:
   ```
   python main.py add-source --type youtube --identifier "https://youtube.com/@SomeChannel" --label "Some Channel"
   ```
   Run `python main.py list-sources` to confirm.

2. **Telegram, if you want it active** (currently `.env` has `TELEGRAM_API_ID`/`API_HASH`/`PHONE` all empty, so it's fully skipped right now):
   - Get API credentials from https://my.telegram.org and add them to `.env`.
   - Add channels: `python main.py add-source --type telegram --identifier "@somechannel" --label "..."`.
   - **For local/manual use**, that's enough — the existing interactive menu option 3 still works exactly as before, with its normal login-code prompt on first use.
   - **For the scheduled GitHub Actions workflow**, you additionally need a `TELEGRAM_SESSION_STRING`, because the workflow can never answer an interactive login-code prompt. Generate one once, locally, after you've already logged in interactively (e.g. via menu option 3, which creates `strategy_harvester_session.session`), with a short one-off script:
     ```python
     from telethon.sync import TelegramClient
     from telethon.sessions import StringSession
     with TelegramClient("strategy_harvester_session", API_ID, API_HASH) as client:
         print(client.session.save())
     ```
     Copy the printed string into a GitHub secret named `TELEGRAM_SESSION_STRING`. **Treat it like a password** — anyone with it can read your Telegram account.

3. **Add `CLAUDE_CODE_OAUTH_TOKEN` as a GitHub secret** (repo → Settings → Secrets and variables → Actions → New repository secret). This is required for SUBSCRIPTION-mode extraction to work in the new workflow — without it, every extraction call there will fail gracefully (logged, nothing crashes, but nothing gets saved either). I did not invent a value for this — you need to generate it from your own already-authenticated local Claude Code install. If you're not sure how, ask in a Claude Code session locally (`claude`) or check Anthropic's docs for "Claude Code headless / CI authentication."

4. **Optionally add the three Telegram secrets** (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`) plus `TELEGRAM_SESSION_STRING` as GitHub secrets too, if you want the scheduled workflow to actually process Telegram (not just YouTube).

## Assumptions I made that you should double-check

1. **I could not test SUBSCRIPTION mode against a real `claude` CLI call.** The `claude` binary isn't on `PATH` in the sandboxed environment this session runs in (this session itself runs through a different harness, not the standalone CLI) — I verified the code fails gracefully when the CLI is absent (tested, works correctly), but I could **not** confirm:
   - That `claude -p "<prompt>" --output-format json --model claude-haiku-4-5-20251001` is the exact correct invocation for your installed CLI version.
   - That the JSON envelope's result field is actually named `"result"` (this is based on public documentation of Claude Code's headless mode, not something I ran and observed).
   **Test this locally before trusting the pipeline**: run `python main.py` → option 2 (manual text) is unaffected, but you can directly test the new path with:
   ```python
   from extraction.strategy_extractor import extract_strategy
   card = extract_strategy("Buy BTC when RSI < 30 on the 4h chart, sell at RSI > 70.",
                            source_type="manual", source_url="test", force_mode="subscription")
   print(card)
   ```
   If the envelope shape is different, only `_extract_subscription()` in `extraction/strategy_extractor.py` needs adjusting — it's isolated from everything else.

2. **I set `extraction_mode: "subscription"` as the new GLOBAL default**, not just for the new watchlist pipeline. This changes the *existing* interactive flows too (single YouTube video, manual text paste, interactive Telegram, X scraping) — they'll now shell out to `claude` instead of the old manual copy-paste-into-a-chat flow. I believe this is what you wanted given the task explicitly said "switch extraction auth method," but if you'd rather the old interactive flows keep using MANUAL mode and only the new automated pipeline uses SUBSCRIPTION mode, it's a one-line revert: change `config.json`'s `"extraction_mode"` back to `"manual"`, and I can instead make the new headless entry point pass `force_mode="subscription"` explicitly.

3. **The `npm install -g @anthropic-ai/claude-code` install step in the workflow is unverified.** I'm fairly confident this is the correct published package based on general knowledge, but I have no way to test a GitHub Actions run from here. If the package name or the auth mechanism has changed, the "Install Claude Code CLI" step (or the subsequent extraction calls) will fail — check the Actions logs after the first real run.

4. **YouTube watchlist mode skips the Whisper fallback entirely** — only videos with an existing caption track (via `youtube-transcript-api`) get processed automatically. This was a deliberate simplification to keep the CI workflow lightweight (no torch/ffmpeg install) and fast enough for a multi-channel batch job. Videos without captions are still checkpointed (so they're not retried forever) but never get a strategy extracted automatically. If a channel you add relies heavily on auto-captions being absent, you'll want to periodically check it manually via menu option 1 (which still has the full Whisper fallback) instead of relying on the watchlist for that channel.

5. **YouTube's checkpoint logic assumes `fetch_channel_videos()` returns newest-first, consistently.** This relies on yt-dlp's channel-listing order, which the existing code already assumed (see its docstring) — I didn't change that assumption, just built the checkpoint diff on top of it. If yt-dlp's ordering is ever inconsistent for a given channel, the diff could either reprocess an old video or (less likely) skip a genuinely new one. Worth an eye if a channel's checkpoint ever looks wrong in `python main.py list-sources`.

6. **Telegram's per-run message limit reuses the existing `messages_to_fetch` config value (default 50)**, both for a source's first run and for ongoing incremental checks. If a channel posts more than 50 new messages between two workflow runs (unlikely at a 4-hour cadence for most trading channels, but possible), the oldest of that batch would be skipped and never revisited, since the checkpoint advances to the newest message seen regardless. Raise `messages_to_fetch` in `config.json` if that's a concern for a specific channel.

## Explicitly not built (per your scope)

- **No strategy-card deduplication** — the same strategy idea appearing in multiple watchlist sources still creates multiple independent cards. Phase 2.
- **No discovery/auto-suggest-new-sources logic** — you have to explicitly `add-source` every channel; nothing suggests candidates. Later phase.
- **X/Twitter is completely unchanged** — `twitter_reader.py` and `trusted_x_accounts` were not touched, read, or wired into any of the above.
