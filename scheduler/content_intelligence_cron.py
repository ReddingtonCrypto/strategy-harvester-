"""
Headless entry point for the Content Intelligence watchlist (Phase 1).

Runs ONE pass: process the YouTube watchlist, then the Telegram watchlist
(skipped gracefully if credentials/session aren't available), extracting via
whatever `extraction_mode` is configured in config.json (SUBSCRIPTION by
default) and saving any Strategy Cards found.

Designed to be triggered by .github/workflows/content_intelligence.yml on a
schedule; also runnable locally:

    python -m scheduler.content_intelligence_cron

A failure on one source never stops the others (see
ingestion/youtube_reader.py:process_watchlist and
ingestion/telegram_reader.py:process_watchlist) — this entry point only
fails (non-zero exit) on a genuine setup problem, e.g. the database can't be
initialised at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Load .env from the project root when present (mirrors runner_cron.py)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("⚠️  python-dotenv not installed; relying on process env vars.")
        return
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        print("ℹ️  No .env file — relying on process env vars (CI mode).")


def main() -> int:
    _load_env()

    from storage import strategy_store
    from utils.helpers import load_config

    try:
        strategy_store.init()
    except Exception as exc:
        print(f"❌ [ContentIntel] Could not initialise the database: {exc}")
        return 1

    config = load_config()
    extraction_mode = config.get("extraction_mode")

    print("=" * 70)
    print("StrategyHarvester — Content Intelligence watchlist run")
    print(f"Extraction mode : {extraction_mode or '(legacy config default)'}")
    print("=" * 70)

    from ingestion import telegram_reader, youtube_reader

    print("\n📺 YouTube watchlist...")
    yt_summary = youtube_reader.process_watchlist(extraction_mode=extraction_mode)
    print(f"   sources={yt_summary['sources_checked']} "
          f"videos={yt_summary['videos_processed']} "
          f"strategies={yt_summary['strategies_found']} "
          f"errors={len(yt_summary['errors'])}")
    for err in yt_summary["errors"]:
        print(f"   ⚠️  {err}")

    print("\n📡 Telegram watchlist...")
    tg_summary = telegram_reader.process_watchlist(extraction_mode=extraction_mode)
    if tg_summary.get("skipped_reason"):
        print(f"   skipped: {tg_summary['skipped_reason']}")
    else:
        print(f"   sources={tg_summary['sources_checked']} "
              f"messages={tg_summary['messages_processed']} "
              f"strategies={tg_summary['strategies_found']} "
              f"errors={len(tg_summary['errors'])}")
        for err in tg_summary["errors"]:
            print(f"   ⚠️  {err}")

    total_found = yt_summary["strategies_found"] + tg_summary["strategies_found"]
    print(f"\n✅ Run complete — {total_found} new strategy card(s) saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
