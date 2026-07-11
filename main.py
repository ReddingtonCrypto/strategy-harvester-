"""
StrategyHarvester — terminal entry point (Phase 1).

Provides an interactive menu to ingest content from various sources, extract
trading strategies into Strategy Cards, review/approve them, export to JSON,
and launch the API server.

Run with:
    python main.py
"""

from __future__ import annotations

import sys

# Load environment variables from .env as early as possible.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print("⚠️  python-dotenv not installed — .env values will not be loaded.")

from extraction.strategy_extractor import extract_strategy
from storage import strategy_store

# `rich` is optional; degrade gracefully to plain printing if it is missing.
try:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    console = None
    _HAS_RICH = False


def _print(msg: str = "") -> None:
    """Print using rich if available, otherwise plain print."""
    if _HAS_RICH:
        console.print(msg)
    else:
        print(msg)


MENU = """
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
23. Refresh Top 50 Coins
24. Exit
"""


# --- Menu actions --------------------------------------------------------

def action_youtube_menu() -> None:
    """YouTube submenu: single video or whole-channel bulk pull."""
    submenu = """
--- Add YouTube Content ---
1. Single video (paste link)
2. Whole channel (bulk pull)
3. Back
"""
    while True:
        _print(submenu)
        choice = input("Choose [1-3]: ").strip()
        if choice == "1":
            action_add_youtube_single()
        elif choice == "2":
            action_add_youtube_channel()
        elif choice == "3":
            break
        else:
            _print("⚠️  Invalid choice. Pick 1-3.")


def action_add_youtube_single() -> None:
    """Ingest a single YouTube video and extract a strategy."""
    from ingestion.base_reader import IngestionError
    from ingestion.youtube_reader import YouTubeReader

    url = input("Enter YouTube URL: ").strip()
    if not url:
        _print("⚠️  No URL entered.")
        return
    try:
        text = YouTubeReader().read(url)
    except IngestionError as exc:
        _print(f"❌ {exc}")
        return

    card = extract_strategy(text, source_type="youtube", source_url=url)
    if card:
        strategy_store.save_card(card)


def action_add_youtube_channel() -> None:
    """Bulk-pull a YouTube channel and extract strategies from its videos."""
    from ingestion.youtube_reader import bulk_process_channel, fetch_channel_videos
    from utils.helpers import load_config

    url = input("Enter YouTube channel URL (e.g. youtube.com/@name): ").strip()
    if not url:
        _print("⚠️  No URL entered.")
        return

    videos = fetch_channel_videos(url)
    if not videos:
        _print("⚠️  No videos found (channel private/empty or network issue).")
        return

    default = int(load_config().get("bulk_channel_default_limit", 20))
    _print(f"Found {len(videos)} videos. Process how many?")
    _print(f"  [A]ll / [number] (e.g. 10) / Enter for default ({default})")
    choice = input("> ").strip().lower()
    if choice in ("a", "all"):
        limit = len(videos)
    elif choice.isdigit():
        limit = int(choice)
    else:
        limit = default

    bulk_process_channel(url, limit=limit)


def action_add_text() -> None:
    """Ingest manual notes (pasted text or a .txt file) and extract."""
    from ingestion.base_reader import IngestionError
    from ingestion.text_reader import TextReader

    choice = input("Read from (1) pasted text or (2) a .txt file? [1/2]: ").strip()
    content, file_path = "", ""
    if choice == "2":
        file_path = input("Enter path to .txt file: ").strip()
    else:
        _print("Paste your text. End with a line containing only 'END':")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == "END":
                break
            lines.append(line)
        content = "\n".join(lines)

    try:
        text = TextReader().read(content=content, file_path=file_path)
    except IngestionError as exc:
        _print(f"❌ {exc}")
        return

    label = file_path or "manual_paste"
    card = extract_strategy(text, source_type="manual", source_url=label)
    if card:
        strategy_store.save_card(card)


def action_add_telegram() -> None:
    """Ingest recent messages from a public Telegram channel and extract."""
    from ingestion.base_reader import IngestionError
    from ingestion.telegram_reader import TelegramReader
    from utils.helpers import load_config

    channel = input("Enter public channel username (e.g. @somechannel): ").strip()
    if not channel:
        _print("⚠️  No channel entered.")
        return

    limit = load_config().get("messages_to_fetch", 50)
    try:
        text = TelegramReader().read(channel, limit=limit)
    except IngestionError as exc:
        _print(f"❌ {exc}")
        return

    card = extract_strategy(text, source_type="telegram", source_url=channel)
    if card:
        strategy_store.save_card(card)


def action_view_cards() -> None:
    """Display all Strategy Cards in a table."""
    cards = strategy_store.list_cards()
    if not cards:
        _print("ℹ️  No Strategy Cards yet.")
        return

    if _HAS_RICH:
        table = Table(title=f"Strategy Cards ({len(cards)})")
        for col in ("ID", "Name", "Source", "Timeframe", "Conf.", "Status"):
            table.add_column(col)
        for c in cards:
            table.add_row(
                c.id, c.name, c.source_type, c.timeframe or "-",
                str(c.confidence_score), c.status,
            )
        console.print(table)
    else:
        for c in cards:
            print(f"[{c.id}] {c.name} | {c.source_type} | "
                  f"{c.timeframe or '-'} | conf={c.confidence_score} | {c.status}")


def action_review() -> None:
    """Approve or reject pending strategies one at a time."""
    cards = [c for c in strategy_store.list_cards() if c.status == "pending_review"]
    if not cards:
        _print("ℹ️  No strategies pending review.")
        return

    for c in cards:
        _print(f"\n--- {c.name} (id={c.id}) ---")
        _print(f"Source     : {c.source_type} — {c.source_url}")
        _print(f"Indicators : {', '.join(c.indicators) or '-'}")
        _print(f"Entry      : {', '.join(c.entry_conditions) or '-'}")
        _print(f"Exit       : {', '.join(c.exit_conditions) or '-'}")
        _print(f"Timeframe  : {c.timeframe or '-'}  Assets: {', '.join(c.assets) or '-'}")
        _print(f"Confidence : {c.confidence_score}")
        choice = input("(a)pprove / (r)eject / (s)kip / (q)uit: ").strip().lower()
        if choice == "a":
            strategy_store.approve_card(c.id)
        elif choice == "r":
            strategy_store.reject_card(c.id)
        elif choice == "q":
            break
        else:
            _print("⏭️  Skipped.")


def action_run_backtest() -> None:
    """Pick a strategy and run a full backtest against Binance data."""
    cards = strategy_store.list_cards()
    if not cards:
        _print("ℹ️  No Strategy Cards to backtest yet.")
        return

    _print("Available strategies:")
    for c in cards:
        flag = "✅" if c.approved else "  "
        _print(f"  {flag} [{c.id}] {c.name} ({c.status})")

    sid = input("Enter strategy id to backtest: ").strip()
    card = strategy_store.get_card(sid)
    if card is None:
        _print(f"⚠️  No strategy found with id={sid}.")
        return
    if not card.approved:
        proceed = input("⚠️  Strategy is not approved. Backtest anyway? [y/N]: ")
        if proceed.strip().lower() != "y":
            return

    default_asset = f"{card.assets[0]}/USDT" if card.assets else "BTC/USDT"
    asset = input(f"Asset [{default_asset}]: ").strip() or default_asset
    default_tf = card.timeframe or "4h"
    timeframe = input(f"Timeframe [{default_tf}]: ").strip() or default_tf

    # Heavy imports (pandas/ccxt) are loaded only when actually backtesting.
    from backtesting.backtest_runner import run_backtest

    run_backtest(card, asset, timeframe)


def action_view_reports() -> None:
    """List all saved backtest reports, with the option to view one in full."""
    from models.backtest_report import BacktestReport
    from backtesting.report_generator import print_report

    cards = [c for c in strategy_store.list_cards() if c.backtest_result]
    if not cards:
        _print("ℹ️  No backtest reports yet. Run a backtest first (option 6).")
        return

    if _HAS_RICH:
        table = Table(title=f"Backtest Reports ({len(cards)})")
        for col in ("ID", "Strategy", "Asset", "TF", "Win%", "Trades", "Verdict"):
            table.add_column(col)
        for c in cards:
            r = c.backtest_result
            table.add_row(
                c.id, str(r.get("strategy_name", c.name)), str(r.get("asset", "-")),
                str(r.get("timeframe", "-")), str(r.get("win_rate", "-")),
                str(r.get("total_trades", "-")), str(r.get("verdict", "-")),
            )
        console.print(table)
    else:
        for c in cards:
            r = c.backtest_result
            print(f"[{c.id}] {r.get('strategy_name')} | {r.get('asset')} | "
                  f"win {r.get('win_rate')}% | {r.get('verdict')}")

    sid = input("Enter an id to view the full report (or Enter to skip): ").strip()
    if sid:
        card = strategy_store.get_card(sid)
        if card and card.backtest_result:
            print_report(BacktestReport.from_dict(card.backtest_result))
        else:
            _print(f"⚠️  No report found for id={sid}.")


def action_view_passed() -> None:
    """Show only strategies whose latest backtest verdict is PASS."""
    passed = [
        c for c in strategy_store.list_cards()
        if isinstance(c.backtest_result, dict)
        and c.backtest_result.get("verdict") == "PASS"
    ]
    if not passed:
        _print("ℹ️  No strategies have PASSED a backtest yet.")
        return

    _print(f"✅ {len(passed)} PASSED strateg"
           f"{'y' if len(passed) == 1 else 'ies'} (ready for signals):")
    for c in passed:
        r = c.backtest_result
        _print(f"  • [{c.id}] {c.name} — {r.get('asset')} {r.get('timeframe')} "
               f"| win {r.get('win_rate')}% | return ${r.get('total_return')}")


def action_export() -> None:
    """Export all Strategy Cards to a timestamped JSON file."""
    strategy_store.export_to_json()


def action_start_signal_engine() -> None:
    """Start the 24/7 live signal engine (runs until Ctrl+C)."""
    from alerts import telegram_alert
    from scheduler.runner import start_scheduler

    _print("⚡ Live Signal Engine")
    if telegram_alert.is_configured():
        if input("Send a Telegram test message first? [Y/n]: ").strip().lower() != "n":
            ok = telegram_alert.send_test_message()
            _print("✅ Test sent — check your phone." if ok
                   else "⚠️  Test failed. Check TELEGRAM_BOT_TOKEN / CHAT_ID in .env.")
    else:
        _print("⚠️  Telegram not configured (TELEGRAM_BOT_TOKEN / CHAT_ID missing "
               "in .env). The engine will scan but cannot send alerts.")

    if input("Start the engine now? [y/N]: ").strip().lower() != "y":
        _print("Cancelled.")
        return
    start_scheduler()  # blocks until Ctrl+C, then returns to the menu


def action_view_live_signals() -> None:
    """Show all currently ACTIVE signals in a table."""
    from signals import signal_store

    signals = signal_store.get_active_signals()
    if not signals:
        _print("ℹ️  No active signals right now.")
        return

    if _HAS_RICH:
        table = Table(title=f"Active Signals ({len(signals)})")
        for col in ("Asset", "TF", "Type", "Price", "Conf", "Trend", "Conflu", "Expires"):
            table.add_column(col)
        for s in signals:
            table.add_row(
                s.asset, s.timeframe, s.signal_type, f"{s.current_price:g}",
                str(s.confidence_score), s.market_trend,
                str(s.confluence_count), s.date_expires[11:16],
            )
        console.print(table)
    else:
        for s in signals:
            print(f"{s.asset} {s.timeframe} {s.signal_type} @ {s.current_price:g} "
                  f"conf {s.confidence_score} {s.market_trend}")


def action_view_signal_history() -> None:
    """Show the last 20 signals with their tracked outcomes."""
    from signals import signal_store

    signals = signal_store.get_recent(limit=20)
    if not signals:
        _print("ℹ️  No signals recorded yet.")
        return

    if _HAS_RICH:
        table = Table(title=f"Signal History (last {len(signals)})")
        for col in ("Time (UTC)", "Asset", "Type", "Conf", "Status",
                    "1H", "4H", "24H", "Result"):
            table.add_column(col)
        for s in signals:
            table.add_row(
                s.date_generated[5:16], s.asset, s.signal_type,
                str(s.confidence_score), s.signal_status,
                _fmt_pct(s.outcome_1h), _fmt_pct(s.outcome_4h),
                _fmt_pct(s.outcome_24h), s.outcome_result or "—",
            )
        console.print(table)
    else:
        for s in signals:
            print(f"{s.date_generated} {s.asset} {s.signal_type} "
                  f"{s.signal_status} result={s.outcome_result or '-'}")


def _fmt_pct(value) -> str:
    """Format an outcome percentage (or '—' when not yet tracked)."""
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "—"


def action_run_learning() -> None:
    """Manually run the learning analysis for a chosen strategy."""
    from learning.adaptation_engine import generate_adaptation, format_changes

    cards = strategy_store.list_cards()
    if not cards:
        _print("ℹ️  No strategies to analyze yet.")
        return
    _print("Strategies:")
    for c in cards:
        _print(f"  [{c.id}] {c.name} v{c.version} ({c.status})")
    sid = input("Enter strategy id to analyze: ").strip()
    card = strategy_store.get_card(sid)
    if card is None:
        _print(f"⚠️  No strategy found with id={sid}.")
        return

    _print("🔬 Running outcome analysis + pattern finding...")
    # Build (and persist) the suggestion without alerting yet.
    suggestion = generate_adaptation(sid, send_telegram=False)
    if suggestion is None:
        _print("ℹ️  No suggestion produced (need ≥10 outcomes and a meaningful "
               "projected improvement).")
        return

    _print(f"\n📊 {suggestion['parent_strategy_name']}")
    _print(f"   Current win rate  : {suggestion['current_win_rate']}%")
    _print(f"   Projected win rate: {suggestion['projected_win_rate']}%")
    _print(f"   Improvement       : +{suggestion['improvement_pct']}%")
    _print("   Key findings:")
    for f in suggestion.get("_key_findings", []):
        _print(f"     - {f}")
    _print("   Suggested changes:")
    for ch in format_changes(suggestion.get("suggested_changes", {})):
        _print(f"     - {ch}")

    if input("\nSend to Telegram for approval? [y/N]: ").strip().lower() == "y":
        from learning.adaptation_engine import send_approval_request
        from storage import database as db
        mid = send_approval_request(suggestion, card.version,
                                    suggestion.get("_key_findings", []))
        if mid:
            db.update_suggestion(suggestion["id"], {"telegram_message_id": str(mid)})
            _print("✅ Approval request sent to Telegram.")
        else:
            _print("⚠️  Could not send (check Telegram config).")
    else:
        _print(f"Saved as PENDING suggestion (id={suggestion['id']}).")


def action_view_lineage() -> None:
    """Show the full version tree for a strategy family."""
    from learning.version_manager import get_strategy_lineage

    sid = input("Enter a strategy id (any version in the family): ").strip()
    if not sid:
        return
    tree = get_strategy_lineage(sid)
    original = tree.get("original")
    if not original:
        _print(f"⚠️  No strategy found with id={sid}.")
        return

    _print(f"\n🌳 Lineage for {original['name']} (root id={original['id']})")
    _print(f"   Total versions: {tree['total_versions']}")
    _print(f"   Best performing: {tree.get('best_performing_version') or '—'}")

    def _row(c: dict) -> str:
        bt = c.get("backtest_result") or {}
        wr = bt.get("win_rate", "—") if isinstance(bt, dict) else "—"
        return (f"   v{c.get('version')} [{c.get('id')}] {c.get('status')} "
                f"| win rate {wr} | adapted={c.get('is_adapted')}")

    _print("   " + "-" * 40)
    _print(_row(original))
    for v in tree.get("versions", []):
        _print(_row(v))


def action_view_suggestions() -> None:
    """Show adaptation suggestions grouped by status."""
    from storage import database as db

    for status in ("PENDING", "APPROVED", "REJECTED"):
        items = db.list_suggestions(status)
        _print(f"\n=== {status} ({len(items)}) ===")
        for s in items:
            _print(f"  [{s['id']}] {s['parent_strategy_name']} "
                   f"| {s['current_win_rate']}% → {s['projected_win_rate']}% "
                   f"(+{s['improvement_pct']}%)"
                   + (f" → new id {s['new_strategy_id']}"
                      if s.get("new_strategy_id") else ""))


def action_add_image() -> None:
    """Add a strategy from a chart/post image + optional notes (Part A)."""
    from ingestion import image_reader

    image_data = image_reader.read_image_input()
    if not image_data:
        return
    parsed = image_reader.extract_from_image(image_data)
    if not parsed:
        return

    _print(f"\n🖼  Image type : {parsed.get('image_type', 'unknown')}")
    _print(f"👁  Observed   : {parsed.get('visual_observations', '')}")
    if parsed.get("notes"):
        _print(f"📝 Notes      : {parsed.get('notes')}")

    card = image_reader.build_and_save_card(parsed, image_data)
    if not card:
        return

    _print(f"\n📋 {card.name}  (confidence {card.confidence_score})")
    _print(f"   Indicators: {', '.join(card.indicators) or '-'}")
    _print(f"   Entry     : {', '.join(card.entry_conditions) or '-'}")
    _print(f"   Exit      : {', '.join(card.exit_conditions) or '-'}")
    _print(f"   Timeframe : {card.timeframe or '-'}   "
           f"Assets: {', '.join(card.assets) or '-'}")

    if input("\nSend to backtest pipeline? (y/n): ").strip().lower() == "y":
        default_asset = f"{card.assets[0]}/USDT" if card.assets else "BTC/USDT"
        asset = input(f"Asset [{default_asset}]: ").strip() or default_asset
        tf = input(f"Timeframe [{card.timeframe or '4h'}]: ").strip() \
            or (card.timeframe or "4h")
        from backtesting.backtest_runner import run_backtest
        run_backtest(card, asset, tf)


def action_manage_twitter() -> None:
    """Twitter/X account management + scraping submenu (Part B)."""
    from ingestion import twitter_reader

    submenu = """
--- Twitter/X Accounts ---
1. Add trusted account (@username)
2. Remove trusted account
3. View trusted accounts
4. Scrape all accounts now
5. Scrape single account
6. Back to main menu
"""
    while True:
        _print(submenu)
        choice = input("Choose [1-6]: ").strip()
        if choice == "1":
            u = input("Enter @username: ").strip()
            if u:
                twitter_reader.add_trusted_account(u)
        elif choice == "2":
            u = input("Enter @username to remove: ").strip()
            if u:
                twitter_reader.remove_trusted_account(u)
        elif choice == "3":
            accounts = twitter_reader.list_trusted_accounts()
            if accounts:
                for a in accounts:
                    _print(f"  • @{a}")
            else:
                _print("  (no trusted accounts yet)")
        elif choice == "4":
            twitter_reader.scrape_all_trusted_accounts()
        elif choice == "5":
            u = input("Enter @username: ").strip()
            if u:
                twitter_reader.scrape_single_account(u)
        elif choice == "6":
            break
        else:
            _print("⚠️  Invalid choice. Pick 1-6.")


def action_refresh_top_coins() -> None:
    """Fetch the top 50 USDT spot coins by 24h volume and save to config."""
    from backtesting.data_fetcher import fetch_top_usdt_coins
    from utils.helpers import load_config, save_config

    _print("📡 Fetching top 50 USDT coins by 24h volume from Binance...")
    coins = fetch_top_usdt_coins(50)
    if not coins:
        _print("❌ Could not fetch coins (network/API issue).")
        return
    config = load_config()
    config["default_assets"] = coins
    if save_config(config):
        _print(f"✅ Saved {len(coins)} coins to config 'default_assets':")
        _print("   " + ", ".join(coins))
    else:
        _print("⚠️  Fetched but failed to write config.json.")


def action_add_local_media() -> None:
    """Transcribe a local video/audio file (Whisper) and extract a strategy."""
    from ingestion.media_reader import read_local_media

    path = input("Enter path to local video/audio file: ").strip()
    if not path:
        _print("⚠️  No path entered.")
        return

    result = read_local_media(path)
    if not result:
        return

    card = extract_strategy(result["text"], source_type=result["source_type"],
                            source_url=result["source_label"])
    if not card:
        return
    strategy_store.save_card(card)

    _print(f"\n📋 {card.name}  (confidence {card.confidence_score})")
    _print(f"   Indicators: {', '.join(card.indicators) or '-'}")
    _print(f"   Entry     : {', '.join(card.entry_conditions) or '-'}")
    _print(f"   Exit      : {', '.join(card.exit_conditions) or '-'}")

    if input("\nSend to backtest pipeline? (y/n): ").strip().lower() == "y":
        default_asset = f"{card.assets[0]}/USDT" if card.assets else "BTC/USDT"
        asset = input(f"Asset [{default_asset}]: ").strip() or default_asset
        tf = input(f"Timeframe [{card.timeframe or '4h'}]: ").strip() \
            or (card.timeframe or "4h")
        from backtesting.backtest_runner import run_backtest
        run_backtest(card, asset, tf)


def action_view_regimes() -> None:
    """Show the current market regime per asset/timeframe + recent history."""
    from backtesting import data_fetcher
    from signals.regime_classifier import classify_regime
    from storage import database as db
    from utils.helpers import load_config

    config = load_config()
    assets = config.get("default_assets", ["BTC", "ETH", "SOL", "BNB"])
    tfs = config.get("default_timeframes", ["1H", "4H", "1D"])

    _print("🌍 Current market regimes (live):")
    for a in assets:
        sym = f"{a}/USDT"
        for tf in tfs:
            df = data_fetcher.fetch_latest_ohlcv(sym, tf.lower(), limit=200)
            if df is None or len(df) < 30:
                _print(f"  {sym} {tf}: (no data)")
                continue
            r = classify_regime(df, a)
            suit = "✅" if r["suitable_for_signals"] else "❌"
            _print(f"  {sym} {tf}: {r['regime']} {suit} "
                   f"(conf {r['confidence']}) — {r['reason']}")

    if assets:
        hist = db.get_regime_history(assets[0], limit=10)
        if hist:
            _print(f"\n🕘 Recent regime history for {assets[0]}:")
            for h in hist:
                _print(f"  {h['recorded_at']}  {h['asset']} {h['timeframe']}: "
                       f"{h['regime']} ({h['confidence']})")


def action_run_optimization() -> None:
    """Run the parameter optimizer for a strategy and show results."""
    from learning.parameter_optimizer import run_full_optimization

    cards = strategy_store.list_cards()
    if not cards:
        _print("ℹ️  No strategies to optimize.")
        return
    for c in cards:
        _print(f"  [{c.id}] {c.name} v{c.version}")
    sid = input("Enter strategy id to optimize: ").strip()
    card = strategy_store.get_card(sid)
    if card is None:
        _print(f"⚠️  No strategy found with id={sid}.")
        return

    _print("🔧 Running parameter optimization (this runs many mini-backtests)...")
    best = run_full_optimization(sid)
    if not best:
        _print("ℹ️  No optimization produced (no tunable parameters or "
               "insufficient data).")
        return

    _print(f"\n📊 Best on {best['asset']} {best['timeframe']}:")
    _print(f"   Original : {best['original_params']}  → {best['original_win_rate']}%")
    _print(f"   Optimal  : {best['optimal_params']}  → {best['optimal_win_rate']}%")
    _print(f"   Improvement: +{best['improvement']}%  "
           f"({best['tested_combinations']} combinations tested)")
    _print("   (saved to DB, NOT auto-applied)")

    if input("\nSend an adaptation suggestion for approval? (y/n): ")\
            .strip().lower() == "y":
        from learning.adaptation_engine import generate_adaptation
        generate_adaptation(sid, send_telegram=True)


def action_performance_dashboard() -> None:
    """Show trust scores + win rates for all strategies (pure DB, no LLM)."""
    from storage import database as db

    rows = db.list_performance()
    if not rows:
        _print("ℹ️  No performance data yet — strategies need signal outcomes "
               "first. Run the engine and let signals mature.")
        return

    if _HAS_RICH:
        table = Table(title=f"Performance Dashboard ({len(rows)})")
        for col in ("Strategy", "Ver", "Trust", "Win%", "Outcomes",
                    "Best Asset", "Best TF", "Best Mkt", "Avoid"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                str(r["strategy_name"]), str(r["version"]),
                str(r["trust_score"]), str(r["win_rate_overall"]),
                str(r["total_outcomes"]), str(r["best_asset"] or "-"),
                str(r["best_timeframe"] or "-"), str(r["best_market_condition"] or "-"),
                str(r["avoid_condition"] or "-"),
            )
        console.print(table)
    else:
        for r in rows:
            print(f"{r['strategy_name']} v{r['version']}: trust {r['trust_score']} "
                  f"| win {r['win_rate_overall']}% | {r['total_outcomes']} outcomes")


def action_start_api() -> None:
    """Launch the FastAPI server on localhost:8000."""
    try:
        import uvicorn
    except ImportError:
        _print("❌ uvicorn not installed. Run: pip install -r requirements.txt")
        return
    _print("🚀 Starting API server at http://localhost:8000 (Ctrl-C to stop)...")
    try:
        uvicorn.run("api.app:app", host="127.0.0.1", port=8000, reload=False)
    except KeyboardInterrupt:
        _print("\n🛑 API server stopped.")


# --- Main loop -----------------------------------------------------------

ACTIONS = {
    "1": action_youtube_menu,
    "2": action_add_text,
    "3": action_add_telegram,
    "4": action_view_cards,
    "5": action_review,
    "6": action_run_backtest,
    "7": action_view_reports,
    "8": action_view_passed,
    "9": action_export,
    "10": action_start_api,
    "11": action_start_signal_engine,
    "12": action_view_live_signals,
    "13": action_view_signal_history,
    "14": action_run_learning,
    "15": action_view_lineage,
    "16": action_view_suggestions,
    "17": action_add_image,
    "18": action_manage_twitter,
    "19": action_view_regimes,
    "20": action_run_optimization,
    "21": action_performance_dashboard,
    "22": action_add_local_media,
    "23": action_refresh_top_coins,
}


def _run_cli(argv: list[str]) -> int:
    """Handle headless CLI subcommands (Phase 1 watchlist source management).

    Separate from the interactive menu below — `python main.py` with no
    arguments still launches the menu unchanged; `python main.py <command>`
    runs one action and exits. Returns a process exit code.
    """
    import argparse

    from utils.helpers import today_str

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="StrategyHarvester headless commands. Run with no "
                     "arguments for the interactive menu instead.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser(
        "add-source", help="Add a source to the content-intelligence watchlist.")
    add_p.add_argument(
        "--type", required=True, choices=["youtube", "telegram", "twitter"],
        help="Source type. (The older config.json trusted_x_accounts list "
             "and menu option 18 still work independently of this — adding "
             "an X account here is a separate watchlist entry.)")
    add_p.add_argument(
        "--identifier", required=True,
        help="YouTube channel URL/id, a Telegram @channel username, or an "
             "X @username.")
    add_p.add_argument("--label", default="", help="Optional human-readable name.")

    sub.add_parser("list-sources", help="List all watchlist sources.")

    args = parser.parse_args(argv)

    strategy_store.init()
    from storage import database as db

    if args.command == "add-source":
        db.add_source(args.type, args.identifier, args.label, today_str())
        print(f"✅ Added {args.type} source: {args.identifier}"
              + (f" ({args.label})" if args.label else ""))
        return 0

    if args.command == "list-sources":
        sources = db.list_sources()
        if not sources:
            print("No sources in the watchlist yet. Add one with:\n"
                  '  python main.py add-source --type youtube --identifier '
                  '"https://youtube.com/@name" --label "Some Channel"')
            return 0
        for s in sources:
            status = "active" if s["active"] else "inactive"
            checked = s["last_checked_at"] or "never"
            item = s["last_item_id"] or "-"
            print(f"[{s['id']}] {s['source_type']:8} {s['identifier']:40} "
                  f"({status}) last_checked={checked} last_item_id={item}")
        return 0

    return 1  # pragma: no cover — argparse enforces a valid subcommand


def main() -> None:
    """Run the interactive terminal menu loop."""
    # Make sure the database/schema exists before anything else.
    strategy_store.init()

    while True:
        _print(MENU)
        choice = input("Choose an option [1-24]: ").strip()

        if choice == "24":
            _print("👋 Goodbye!")
            break

        action = ACTIONS.get(choice)
        if action is None:
            _print("⚠️  Invalid choice. Please pick 1-24.")
            continue

        try:
            action()
        except KeyboardInterrupt:
            _print("\n↩️  Cancelled — back to menu.")
        except Exception as exc:  # never crash the menu on an action error
            _print(f"❌ Unexpected error: {exc}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(_run_cli(sys.argv[1:]))
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        sys.exit(0)
