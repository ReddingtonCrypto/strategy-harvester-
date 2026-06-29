"""
Cron entry point (GitHub Actions / any external scheduler).

Runs EXACTLY ONE scan cycle, then exits. There is no infinite loop and no
background listener — the *scheduler* (GitHub Actions cron) is responsible for
re-invoking this every hour. This makes the process stateless and short-lived,
which is exactly what a CI cron runner needs.

  python -m scheduler.runner_cron

One cycle does (same core as the 24/7 runner's `_do_cycle`):
  1. init DB (auto-backup + migrations) + load strategy store
  2. expire_old_signals   — retire signals past their window
  3. run_scan             — scan all coins/timeframes, send Telegram alerts
  4. update_signal_outcomes — score still-open signals against latest price

State (the SQLite DB) lives on disk; the GitHub Actions workflow commits it
back to the repo after this process exits, so it persists between runs.

Exit codes: 0 = clean (even if zero signals), 1 = fatal error before/around
the scan. A fatal error also pings Telegram if it's configured.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _setup_console() -> None:
    """UTF-8 stdout/stderr so emoji in logs don't crash on Windows / CI."""
    for s in ("stdout", "stderr"):
        try:
            getattr(sys, s).reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _load_env() -> None:
    """Load .env from the project root when present (local runs).

    On GitHub Actions there is no .env — secrets arrive as real environment
    variables, so a missing file is fine and we just rely on os.environ.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("ℹ️  python-dotenv not installed; relying on process env vars.")
        return
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"🔑 Loaded secrets from {env_path.name}")
    else:
        print("ℹ️  No .env file — relying on process env vars (CI mode).")


def run_once() -> int:
    """Run a single scan cycle. Returns a process exit code (0 ok, 1 fatal)."""
    _setup_console()
    _load_env()

    from utils import helpers  # noqa: F401 — triggers UTF-8 console setup
    from utils.helpers import load_config
    from storage import strategy_store
    from signals import signal_store
    from signals.market_scanner import run_scan
    from alerts import telegram_alert

    started = datetime.now(timezone.utc)
    coins = load_config().get("default_assets", [])
    print("=" * 60)
    print("  StrategyHarvester — CRON (single scan cycle)")
    print(f"  {len(coins)} coins watched · {started:%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 60)

    if not telegram_alert.is_configured():
        print("⚠️  Telegram not configured — alerts will be skipped. "
              "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    try:
        # init_db() runs an auto-backup + migrations on every startup.
        strategy_store.init()

        # The one cycle: expire → scan (sends alerts) → score outcomes.
        signal_store.expire_old_signals()
        summary = run_scan()
        signal_store.update_signal_outcomes()
    except Exception as exc:  # noqa: BLE001 — surface, notify, fail the job
        print(f"❌ [Cron] Scan cycle failed: {exc!r}")
        try:
            if telegram_alert.is_configured():
                telegram_alert.send_error_alert(f"Cron scan failed: {exc}")
        except Exception:
            pass
        return 1

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"✅ [Cron] Scan cycle complete in {elapsed:.0f}s — "
          f"{summary.get('signals_found', 0)} signal(s), "
          f"{summary.get('alerts_sent', 0)} alert(s) sent. Exiting.")
    return 0


if __name__ == "__main__":
    sys.exit(run_once())
