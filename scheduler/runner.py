"""
24/7 scheduler (Phase 3).

Drives the live signal engine:
  - immediate first scan, then every `scan_interval_minutes`
  - each cycle: expire_old_signals → run_scan → update_signal_outcomes
  - heartbeat every `heartbeat_interval_minutes`
  - daily summary at `daily_summary_time_utc` (00:00 UTC)
  - any scan error → Telegram error alert, wait 5 minutes, retry (never crash)
  - Ctrl+C → clean shutdown + Telegram notice

A hand-rolled loop (rather than the `schedule` library) is used so the
error-retry, immediate-first-run, and UTC-daily behaviours compose cleanly.
"""

from __future__ import annotations

import time
from datetime import timedelta

from alerts import telegram_alert
from signals import signal_store
from signals.market_scanner import _passed_strategies, run_scan
from storage import database as db
from utils.helpers import load_config, parse_utc, utc_now

_ERROR_RETRY_SECONDS = 5 * 60  # wait 5 minutes after a scan error


def _run_daily_adaptation() -> None:
    """Run the full daily learning pipeline (performance → optimize → suggest)."""
    from learning.adaptation_engine import run_daily_adaptation

    print("📚 [Scheduler] Running daily learning + adaptation...")
    try:
        run_daily_adaptation(send_telegram=True)
    except Exception as exc:  # never let it crash the loop
        print(f"⚠️  [Scheduler] Daily adaptation failed: {exc}")
    print("📚 Weekly learning run complete")


def _run_x_scrape() -> None:
    """Scrape all trusted X accounts and notify on any new strategies."""
    from ingestion import twitter_reader

    if not twitter_reader.list_trusted_accounts():
        return  # nothing configured — skip silently
    print("🐦 [Scheduler] Scraping trusted X accounts...")
    summary = twitter_reader.scrape_all_trusted_accounts()
    if summary.get("new_strategies", 0) > 0 and telegram_alert.is_configured():
        lines = ["🐦 X Scrape Complete",
                 f"Accounts checked: {summary['accounts_checked']}",
                 f"New strategies found: {summary['new_strategies']}"]
        for f in summary.get("found", []):
            lines.append(f"→ {f['name']} ({f['username']})")
        lines.append("Run backtest? Check menu option 6.")
        telegram_alert.send_message("\n".join(lines))


def _send_pending_reminder(reminder_days: int) -> None:
    """Remind the user about suggestions pending longer than `reminder_days`."""
    now = utc_now()
    pending = db.list_suggestions("PENDING")
    stale = [s for s in pending
             if (gen := parse_utc(s.get("created_at", ""))) is not None
             and (now - gen) >= timedelta(days=reminder_days)]
    if stale and telegram_alert.is_configured():
        telegram_alert.send_message(
            f"⏰ You have {len(stale)} pending strategy improvement"
            f"{'s' if len(stale) != 1 else ''} waiting for your review.")
        print(f"⏰ [Scheduler] Sent reminder for {len(stale)} pending suggestions.")


def _run_topcoin_refresh() -> None:
    """Refresh default_assets to the current top-50 USDT coins by 24h volume."""
    from backtesting.data_fetcher import fetch_top_usdt_coins
    from utils.helpers import load_config, save_config

    coins = fetch_top_usdt_coins(50)
    if not coins:
        print("⚠️  [Scheduler] Top-50 refresh: fetch returned nothing; "
              "keeping the current watchlist.")
        return
    config = load_config()
    config["default_assets"] = coins
    if not save_config(config):
        print("⚠️  [Scheduler] Top-50 refresh: failed to write config.json.")
        return
    print(f"📊 Top 50 coins refreshed: {coins}")
    if telegram_alert.is_configured():
        telegram_alert.send_message(
            f"📊 Watchlist updated - now scanning {len(coins)} coins "
            f"by 24h volume")
    # The scanner reads default_assets fresh each scan, so the new list is
    # picked up automatically on the next cycle.


def _startup_banner(assets, n_strategies, interval) -> None:
    """Print the startup info block."""
    print("🚀 StrategyHarvester Signal Engine Started")
    print(f"Watching: {' '.join(assets)}")
    print(f"Active strategies: {n_strategies}")
    print(f"Scan interval: {interval} min")
    print("(Press Ctrl+C to stop)\n")


def _do_cycle(interval: int) -> None:
    """Run one scan cycle: expire → scan → outcomes."""
    signal_store.expire_old_signals()
    run_scan()
    signal_store.update_signal_outcomes()


def start_scheduler() -> None:
    """Start the live signal engine loop (blocks until Ctrl+C)."""
    config = load_config()
    assets = config.get("default_assets", ["BTC", "ETH", "SOL", "BNB"])
    interval = int(config.get("scan_interval_minutes", 15))
    heartbeat_every = int(config.get("heartbeat_interval_minutes", 60))
    summary_time = str(config.get("daily_summary_time_utc", "00:00"))
    summary_hour = int(summary_time.split(":")[0]) if ":" in summary_time else 0
    reminder_days = int(config.get("suggestion_reminder_days", 3))
    x_scrape_hours = int(config.get("x_scrape_interval_hours", 6))
    adaptation_time = str(config.get("adaptation_time_utc", "01:00"))
    adaptation_hour = int(adaptation_time.split(":")[0]) if ":" in adaptation_time else 1
    topcoin_time = str(config.get("topcoin_refresh_time_utc", "00:30"))
    _tc = topcoin_time.split(":") if ":" in topcoin_time else ["0", "30"]
    topcoin_hour, topcoin_minute = int(_tc[0]), int(_tc[1])

    _startup_banner(assets, len(_passed_strategies()), interval)

    # Start the Telegram approval listener in the background (Phase 4).
    telegram_alert.start_approval_listener()

    # Announce start on Telegram (doubles as a connectivity check).
    if telegram_alert.is_configured():
        telegram_alert.send_message(
            f"🚀 StrategyHarvester Signal Engine started\n"
            f"👁 Watching: {' '.join(assets)}\n"
            f"⏱ Scan interval: {interval} min")
    else:
        print("⚠️  Telegram not configured — alerts will be skipped. "
              "Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env.")

    last_heartbeat = utc_now()
    # Initialise to today/now so periodic jobs fire at their NEXT due time.
    last_summary_date = utc_now().strftime("%Y-%m-%d")
    last_reminder_date = utc_now().strftime("%Y-%m-%d")
    last_adaptation_date = utc_now().strftime("%Y-%m-%d")
    last_topcoin_date = utc_now().strftime("%Y-%m-%d")
    last_x_scrape = utc_now()

    try:
        while True:
            # --- One scan cycle, wrapped so errors never crash the loop ---
            try:
                _do_cycle(interval)
            except Exception as exc:  # noqa: BLE001 - keep loop alive
                print(f"❌ [Scheduler] Scan error: {exc}")
                if telegram_alert.is_configured():
                    telegram_alert.send_error_alert(str(exc))
                print("⏳ Waiting 5 minutes before retrying...")
                time.sleep(_ERROR_RETRY_SECONDS)
                continue

            now = utc_now()

            # --- Heartbeat (proof of life) -----------------------------
            if (now - last_heartbeat) >= timedelta(minutes=heartbeat_every):
                if telegram_alert.is_configured():
                    telegram_alert.send_heartbeat(interval)
                print("💓 [Scheduler] Heartbeat sent.")
                last_heartbeat = now

            # --- Daily summary at 00:00 UTC ----------------------------
            today = now.strftime("%Y-%m-%d")
            if today != last_summary_date and now.hour == summary_hour:
                summary = signal_store.get_daily_summary()
                if telegram_alert.is_configured():
                    telegram_alert.send_daily_summary(summary)
                print("📊 [Scheduler] Daily summary sent.")
                last_summary_date = today

            # --- Daily Top-50 watchlist refresh at 00:30 UTC (Task 1) --
            if (today != last_topcoin_date and now.hour == topcoin_hour
                    and now.minute >= topcoin_minute):
                _run_topcoin_refresh()
                last_topcoin_date = today

            # --- Daily pending-suggestion reminder ---------------------
            if today != last_reminder_date:
                _send_pending_reminder(reminder_days)
                last_reminder_date = today

            # --- Daily learning + adaptation at 01:00 UTC (Phase 5.5) --
            if today != last_adaptation_date and now.hour == adaptation_hour:
                _run_daily_adaptation()
                last_adaptation_date = today

            # --- X account scraping every N hours (Phase 5) ------------
            if (now - last_x_scrape) >= timedelta(hours=x_scrape_hours):
                _run_x_scrape()
                last_x_scrape = now

            # --- Sleep until next scan ---------------------------------
            print(f"😴 [Scheduler] Sleeping {interval} min until next scan...\n")
            time.sleep(interval * 60)

    except KeyboardInterrupt:
        print("\n🛑 StrategyHarvester stopped cleanly")
        if telegram_alert.is_configured():
            telegram_alert.send_message("🛑 Signal engine stopped by user")
