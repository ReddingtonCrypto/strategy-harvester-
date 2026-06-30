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


def _next_in_label() -> str:
    """Human label for the gap until the next cron run (from config)."""
    import os
    from utils.helpers import load_config
    # GitHub Actions cron is hourly by default; allow a config override note.
    mins = int(load_config().get("cron_interval_minutes", 60))
    if os.environ.get("CRON_NEXT_IN"):
        return os.environ["CRON_NEXT_IN"]
    return f"{mins // 60}h" if mins % 60 == 0 and mins >= 60 else f"{mins}m"


def _maybe_daily_digest(telegram_alert, db, data_source: str) -> None:
    """Send the daily digest once per UTC day (around 00:00), idempotently."""
    from datetime import datetime, timezone
    from utils.helpers import load_config

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if db.get_state("last_digest_date") == today:
        return  # already sent today
    # Fire on/after 00:00 UTC — the first run of a new UTC day qualifies.
    scans_today = db.count_scans_on(today)
    stats = db.get_scan_run_stats()
    coins = load_config().get("default_assets", [])
    digest = {
        "date": today,
        "scans": scans_today,
        "signals": stats.get("signals_today", 0),
        "coins": len(coins),
        "data_source": data_source,
        "strategy": "CRT (observation mode)",
        "healthy": (stats.get("last_status") or "ok") == "ok",
    }
    if not telegram_alert.is_configured():
        return  # nothing to send; don't burn the once-a-day marker
    if telegram_alert.send_daily_digest(digest):
        db.set_state("last_digest_date", today)  # mark only on success
        print(f"📊 [Cron] Daily digest sent for {today}.")
    else:
        print("⚠️  [Cron] Daily digest send failed; will retry next run.")


def run_once() -> int:
    """Run a single scan cycle. Returns a process exit code (0 ok, 1 fatal)."""
    _setup_console()
    _load_env()

    import os
    from utils import helpers  # noqa: F401 — triggers UTF-8 console setup
    from utils.helpers import load_config, utc_now_str
    from storage import strategy_store, database as db
    from signals import signal_store
    from signals.market_scanner import run_scan
    from backtesting import data_fetcher
    from alerts import telegram_alert
    from monitoring import dashboard

    started = datetime.now(timezone.utc)
    coins = load_config().get("default_assets", [])
    on_actions = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    print("=" * 60)
    print("  StrategyHarvester — CRON (single scan cycle)")
    print(f"  {len(coins)} coins watched · {started:%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 60)

    if not telegram_alert.is_configured():
        print("⚠️  Telegram not configured — alerts will be skipped. "
              "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    # Startup notice — one per GitHub Actions run (skipped on local runs so
    # repeated local testing doesn't spam the chat).
    if on_actions and telegram_alert.is_configured():
        telegram_alert.send_startup_notice("GitHub Actions")

    status = "ok"
    summary: dict = {}
    data_source = "n/a"
    try:
        # init_db() runs an auto-backup + migrations on every startup.
        strategy_store.init()

        # The one cycle: expire → scan (sends alerts) → score outcomes.
        signal_store.expire_old_signals()
        summary = run_scan()
        signal_store.update_signal_outcomes()

        # Which data source actually served the scan, and did we fall back?
        data_source = data_fetcher.active_exchange_id() or "n/a"
        primary = data_fetcher.configured_primary()
        if (data_source != "n/a" and primary
                and data_source != primary and telegram_alert.is_configured()):
            telegram_alert.send_fallback_notice(primary, data_source)
            print(f"⚠️  [Cron] Data fallback: {primary} → {data_source}")
    except Exception as exc:  # noqa: BLE001 — surface, notify, record, fail
        status = "error"
        print(f"❌ [Cron] Scan cycle failed: {exc!r}")
        try:
            if telegram_alert.is_configured():
                telegram_alert.send_error_alert(f"Cron scan failed: {exc}")
        except Exception:
            pass

    # --- Monitoring: record the run, heartbeat, digest, dashboard --------
    # Best-effort — monitoring must never change the job's success/failure.
    try:
        db.record_scan_run(
            utc_now_str(), len(coins), int(summary.get("signals_found", 0)),
            int(summary.get("alerts_sent", 0)), data_source, status)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  [Cron] Failed to record scan run: {exc}")

    if status == "ok" and telegram_alert.is_configured():
        try:
            telegram_alert.send_scan_heartbeat(
                len(coins), int(summary.get("alerts_sent", 0)),
                int(summary.get("shadow_logged", 0)), data_source,
                _next_in_label(), summary.get("logged_signals"))
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  [Cron] Heartbeat failed: {exc}")

    try:
        _maybe_daily_digest(telegram_alert, db, data_source)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  [Cron] Daily digest check failed: {exc}")

    try:
        dashboard.generate()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  [Cron] Dashboard generation failed: {exc}")

    if status == "error":
        return 1
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"✅ [Cron] Scan cycle complete in {elapsed:.0f}s — "
          f"{summary.get('signals_found', 0)} signal(s), "
          f"{summary.get('alerts_sent', 0)} alert(s) sent. Exiting.")
    return 0


if __name__ == "__main__":
    sys.exit(run_once())
