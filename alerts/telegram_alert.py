"""
Telegram alert integration (Phase 3).

Sends signal alerts, daily summaries, heartbeats, and error notifications via
the Telegram Bot HTTP API using only `requests` (no extra telegram library).

Setup (.env):
    TELEGRAM_BOT_TOKEN=...   (from @BotFather)
    TELEGRAM_CHAT_ID=...     (your chat id, see README)

Sending is best-effort and resilient: failures retry up to 3 times and then
give up quietly so the scanner never crashes on a Telegram outage.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from models.signal import Signal
from utils.helpers import get_env, load_config, utc_now_str

_BASE_TEMPLATE = "https://api.telegram.org/bot{token}/{method}"
_MAX_RETRIES = 3
_RETRY_WAIT = 5  # seconds between retries (per spec)

# Offset bookmark so getUpdates only returns unseen updates (survives restarts).
_OFFSET_FILE = Path(__file__).resolve().parent.parent / ".tg_offset"


# --- Core API poster -----------------------------------------------------

def _api_post(method: str, payload: dict, *, retries: int = _MAX_RETRIES
              ) -> Optional[dict]:
    """POST to a Telegram Bot API method. Returns parsed JSON or None.

    Retries up to `retries` times (5s apart). Never raises.
    """
    token = get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        print("⚠️  [Telegram] TELEGRAM_BOT_TOKEN not set.")
        return None
    try:
        import requests
    except ImportError:
        print("❌ [Telegram] 'requests' not installed (pip install requests).")
        return None

    url = _BASE_TEMPLATE.format(token=token, method=method)
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, data=payload, timeout=20)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                return data
            print(f"⚠️  [Telegram] {method} failed (HTTP {resp.status_code}): "
                  f"{str(data)[:200]}")
        except Exception as exc:
            print(f"⚠️  [Telegram] {method} error (attempt {attempt}): {exc}")
        if attempt < retries:
            time.sleep(_RETRY_WAIT)
    return None


def _send(text: str) -> bool:
    """Send a plain-text message to the configured chat. Returns success."""
    chat_id = get_env("TELEGRAM_CHAT_ID")
    if not chat_id:
        print("⚠️  [Telegram] TELEGRAM_CHAT_ID not set — message not sent.")
        return False
    result = _api_post("sendMessage", {
        "chat_id": chat_id, "text": text, "disable_web_page_preview": True,
    })
    return result is not None


def send_message_with_buttons(text: str, buttons: list[list[dict]]
                              ) -> Optional[str]:
    """Send a message with an inline keyboard. Returns the message id, or None.

    `buttons` is a list of rows; each button is
    {"text": "...", "callback_data": "..."}.
    """
    chat_id = get_env("TELEGRAM_CHAT_ID")
    if not chat_id:
        print("⚠️  [Telegram] TELEGRAM_CHAT_ID not set — message not sent.")
        return None
    result = _api_post("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "reply_markup": json.dumps({"inline_keyboard": buttons}),
    })
    if result:
        return str(result.get("result", {}).get("message_id"))
    return None


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    """Acknowledge a callback so the button stops showing a loading spinner."""
    _api_post("answerCallbackQuery",
              {"callback_query_id": callback_query_id, "text": text},
              retries=1)


def is_configured() -> bool:
    """True if both bot token and chat id are present in the environment."""
    return bool(get_env("TELEGRAM_BOT_TOKEN") and get_env("TELEGRAM_CHAT_ID"))


def send_test_message() -> bool:
    """Send a connectivity test message (used by the menu before starting)."""
    return _send(f"✅ StrategyHarvester Telegram test — it works!\n"
                 f"🕐 {utc_now_str()} UTC")


# --- Formatting helpers --------------------------------------------------

def _fmt_price(value: float) -> str:
    """Human-friendly price formatting across large and small assets."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:,.6f}".rstrip("0").rstrip(".")


# --- Public alert builders -----------------------------------------------

_SENTIMENT_EMOJI = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪",
                    "MIXED": "🟡"}


_OBSERVATION_LABEL = (
    "⚠️ OBSERVATION MODE - CRT tested on BTC only, small sample. "
    "Not proven on alts or live. Analyze before trading.")


def send_signal_alert(signal: Signal | dict[str, Any],
                      sentiment: Optional[dict[str, Any]] = None,
                      trust: Optional[dict[str, Any]] = None,
                      regime: Optional[dict[str, Any]] = None,
                      observation: bool = False,
                      proof: Optional[dict[str, Any]] = None) -> bool:
    """Format and send a BUY/SELL signal alert (sentiment + trust + regime).

    When `observation` is True an OBSERVATION MODE warning is prepended (the
    strategy is live for monitoring only, not validated for trading).

    `proof` (optional) makes the proven-vs-unproven status visually obvious:
    a proven (backtested) timeframe leads with 🟢 + a BACKTESTED EDGE banner;
    an unproven timeframe leads with 🟡 + a loud "observation only" warning.
    """
    s = signal if isinstance(signal, Signal) else Signal.from_dict(signal)

    # Header emoji: proof status (🟢 proven / 🟡 unproven) when provided,
    # else fall back to direction colour.
    if proof:
        header_emoji = proof.get("emoji", "🟢")
    else:
        header_emoji = "🟢" if s.signal_type == "BUY" else "🔴"
    strength_emoji = {"STRONG": "💪", "MODERATE": "👍", "WEAK": "🤏"}.get(
        s.trend_strength, "")
    volume_line = "✅ Confirmed" if s.volume_confirmation else "❌ Not confirmed"

    # Confidence line, noting the sentiment adjustment if one was applied.
    conf_line = f"🎯 Confidence : {s.confidence_score}/100"
    adj = (sentiment or {}).get("adjustment", 0)
    if sentiment and adj:
        conf_line += f" (adjusted from {s.confidence_score - adj})"

    header = [f"{header_emoji} {s.signal_type} SIGNAL — {s.asset} · {s.timeframe}"]
    # Proof banner first — the single most important line for sizing decisions.
    if proof and proof.get("label"):
        header += [proof["label"]]
    if observation:
        header += ["", _OBSERVATION_LABEL]
    lines = header + [
        "",
        f"📊 Strategy   : {s.strategy_name}",
        f"⏱ Timeframe  : {s.timeframe}",
        f"💰 Entry Zone : {_fmt_price(s.entry_zone_low)} – "
        f"{_fmt_price(s.entry_zone_high)}",
        f"📈 Current    : {_fmt_price(s.current_price)}",
        "",
        f"🌍 Market     : {s.market_trend} {strength_emoji} {s.trend_strength}",
        f"📦 Volume     : {volume_line}",
        conf_line,
    ]

    # Trust score (Phase 5.5) — how much history backs this strategy.
    if trust:
        lines.append(f"⭐ Trust Score : {trust.get('score')}/100 "
                     f"(based on {trust.get('outcomes', 0)} outcomes)")

    # Market regime (Phase 5.5).
    if regime:
        suit = "✅" if regime.get("suitable_for_signals") else "⚠️"
        lines.append(f"📊 Regime     : {regime.get('regime')} {suit} "
                     f"{regime.get('volatility', '')}".rstrip())

    if s.confluence_count >= 2 and s.confluence_strategies:
        lines.append("")
        lines.append(f"🔀 Confluence : {s.confluence_count} strategies agree")
        for name in s.confluence_strategies:
            lines.append(f"   • {name}")

    # X sentiment section (Phase 5) — only when a sentiment read exists.
    if sentiment:
        overall = sentiment.get("overall_sentiment", "NEUTRAL")
        emoji = _SENTIMENT_EMOJI.get(overall, "⚪")
        lines.append("")
        lines.append(f"🐦 X Sentiment: {emoji} {overall} ({adj:+d})")
        for acc in (sentiment.get("per_account") or [])[:6]:
            a_sent = str(acc.get("sentiment", "NEUTRAL")).upper()
            a_emoji = _SENTIMENT_EMOJI.get(a_sent, "⚪")
            user = str(acc.get("username", "")).lstrip("@")
            lines.append(f"   @{user} → {a_emoji} {a_sent.title()}")
        lines.append(f"   Score: {int(sentiment.get('sentiment_score', 0)):+d}/100")

    lines += [
        "",
        f"📌 Source     : {s.source or 'n/a'}",
        f"⏰ Time       : {s.date_generated} UTC",
        "",
        "⚠️ NOT financial advice. DYOR.",
    ]
    return _send("\n".join(lines))


def send_daily_summary(summary_data: dict[str, Any]) -> bool:
    """Format and send the daily summary."""
    d = summary_data or {}
    coins = d.get("coins_watched", []) or []
    top_asset = d.get("top_asset") or "—"
    top_strategy = d.get("top_strategy") or "—"

    text = "\n".join([
        f"📊 DAILY SUMMARY — {utc_now_str()[:10]}",
        "",
        f"Signals today    : {d.get('total', 0)}",
        f"🟢 BUY signals  : {d.get('buy_signals', 0)}",
        f"🔴 SELL signals : {d.get('sell_signals', 0)}",
        f"🏆 Top asset    : {top_asset}",
        f"⭐ Top strategy : {top_strategy}",
        f"🎯 Avg confidence: {d.get('avg_confidence', 0)}/100",
        "",
        f"✅ Active strategies : {d.get('active_strategies', 0)}",
        f"👁 Coins watched    : {' '.join(coins)}",
    ])
    return _send(text)


def send_heartbeat(next_scan_in_minutes: int) -> bool:
    """Send a proof-of-life heartbeat."""
    text = "\n".join([
        "💓 StrategyHarvester alive",
        f"🕐 {utc_now_str()} UTC",
        f"⏭ Next scan: {next_scan_in_minutes} min",
    ])
    return _send(text)


def send_error_alert(error_message: str) -> bool:
    """Send an error notification."""
    text = "\n".join([
        "⚠️ SCANNER ERROR",
        f"{utc_now_str()} UTC",
        f"Error: {error_message}",
        "Retrying in 5 minutes...",
    ])
    return _send(text)


def send_message(text: str) -> bool:
    """Send an arbitrary message (used for startup/shutdown notices)."""
    return _send(text)


# --- Agent monitoring messages (scanner observability) -------------------

def send_scan_heartbeat(coins: int, live_alerts: int, shadow_logged: int,
                        data_source: str, next_in: str,
                        logged_signals: Optional[list[dict[str, Any]]] = None
                        ) -> bool:
    """Proof-of-life after each scan (toggle: config heartbeat_enabled).

    Reports what was actually SAVED — live alerts + shadow-logged — not raw
    detections, and lists each signal WITH its coin name so the dashboard and
    Telegram always agree.

    Example:
      🔍 Scan done 14:02 UTC · 50 coins · 0 live · 1 shadow · data: okx · next 30m
      👻 FRVP WIF/USDT 1h (shadow · BUY)
    """
    if not load_config().get("heartbeat_enabled", True):
        return False
    when = utc_now_str()[11:16]  # HH:MM
    lines = [f"🔍 Scan done {when} UTC · {coins} coins · {live_alerts} live · "
             f"{shadow_logged} shadow · data: {data_source} · next {next_in}"]
    for s in (logged_signals or []):
        emoji = "🟢" if s.get("mode") == "live" else "👻"
        coin = str(s.get("coin", "")).split("/")[0]
        lines.append(f"{emoji} {s.get('strategy','?')} {coin} {s.get('tf','')} "
                     f"({s.get('mode','?')} · {s.get('type','')})")
    return _send("\n".join(lines))


def send_startup_notice(trigger: str = "GitHub Actions") -> bool:
    """Announce that a scanner run has started (sent once per run)."""
    return _send(f"🚀 Scanner started {utc_now_str()} UTC via {trigger}")


def send_fallback_notice(primary: str, actual: str) -> bool:
    """Warn that the primary data source failed and a fallback is in use."""
    return _send(f"⚠️ {utc_now_str()[11:16]} UTC · {primary} failed, "
                 f"using {actual} fallback")


def send_daily_digest(digest: dict[str, Any]) -> bool:
    """Send the once-a-day rollup of scanner activity."""
    d = digest or {}
    health = "healthy ✅" if d.get("healthy", True) else "degraded ⚠️"
    return _send("\n".join([
        f"📊 Daily Report {d.get('date', utc_now_str()[:10])}",
        f"Scans run: {d.get('scans', 0)}",
        f"Signals fired: {d.get('signals', 0)}",
        f"Coins watched: {d.get('coins', 0)}",
        f"Data source: {d.get('data_source', 'n/a')}",
        f"Strategy: {d.get('strategy', 'CRT (observation mode)')}",
        f"Uptime: {health}",
    ]))


# --- Approval listener (Phase 4) -----------------------------------------

_listener_thread: Optional[threading.Thread] = None
_listener_stop = threading.Event()


def _read_offset() -> Optional[int]:
    """Read the saved getUpdates offset, or None if unset."""
    try:
        return int(_OFFSET_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _write_offset(offset: int) -> None:
    """Persist the getUpdates offset."""
    try:
        _OFFSET_FILE.write_text(str(offset))
    except OSError:
        pass


def _dispatch_callback(data: str, cbq_id: str) -> None:
    """Route an inline-button callback to the version manager."""
    # Imported lazily to avoid a circular import at module load.
    from learning import version_manager

    # Daily-report batch buttons (Phase 5.5).
    if data == "approve_all":
        from learning.adaptation_engine import approve_all_pending
        answer_callback_query(cbq_id, "⏳ Approving all...")
        n = approve_all_pending()
        _send(f"✅ Approved all — {n} new version(s) created "
              f"(each needs a fresh backtest).")
        return
    if data == "review_each":
        from learning.adaptation_engine import send_individual_reviews
        answer_callback_query(cbq_id, "👁 Sending each suggestion...")
        n = send_individual_reviews()
        if n == 0:
            _send("ℹ️ No pending suggestions to review.")
        return

    if data.startswith("approve_"):
        sid = data[len("approve_"):]
        answer_callback_query(cbq_id, "⏳ Creating new version...")
        card = version_manager.create_adapted_version(sid)
        if not card:
            _send("⚠️ Could not create the new version (see logs).")
    elif data.startswith("reject_"):
        sid = data[len("reject_"):]
        answer_callback_query(cbq_id, "❌ Rejected.")
        version_manager.reject_suggestion(sid)
    elif data.startswith("view_"):
        strategy_id = data[len("view_"):]
        answer_callback_query(cbq_id, "👁 Sending original...")
        _send_strategy_details(strategy_id)
    else:
        answer_callback_query(cbq_id)


def _dispatch_text(text: str) -> None:
    """Handle 'APPROVE <id>' / 'REJECT <id>' text replies."""
    from learning import version_manager

    parts = text.strip().split()
    if len(parts) != 2:
        return
    verb, sid = parts[0].upper(), parts[1]
    if verb == "APPROVE":
        card = version_manager.create_adapted_version(sid)
        if not card:
            _send("⚠️ Could not create the new version (see logs).")
    elif verb == "REJECT":
        version_manager.reject_suggestion(sid)


def _send_strategy_details(strategy_id: str) -> None:
    """Send a summary of an original strategy to Telegram."""
    from storage import strategy_store

    card = strategy_store.get_card(strategy_id)
    if card is None:
        _send(f"⚠️ Strategy {strategy_id} not found.")
        return
    bt = card.backtest_result if isinstance(card.backtest_result, dict) else {}
    _send("\n".join([
        f"👁 ORIGINAL — {card.name} v{card.version}",
        f"Status   : {card.status}",
        f"Assets   : {', '.join(card.assets) or '-'}",
        f"Timeframe: {card.timeframe or '-'}",
        f"Entry    : {card.entry_rule or '; '.join(card.entry_conditions) or '-'}",
        f"Exit     : {card.exit_rule or '; '.join(card.exit_conditions) or '-'}",
        f"Backtest : win rate {bt.get('win_rate', 'n/a')}% "
        f"({bt.get('verdict', 'not tested')})",
    ]))


def _listen_loop() -> None:
    """Background loop: poll getUpdates and dispatch approvals."""
    poll = int(load_config().get("approval_poll_interval_seconds", 5))
    offset = _read_offset()

    # On first ever run, skip any backlog so old messages aren't re-processed.
    if offset is None:
        drained = _api_post("getUpdates", {"timeout": 0}, retries=1)
        if drained and drained.get("result"):
            offset = drained["result"][-1]["update_id"] + 1
            _write_offset(offset)

    while not _listener_stop.is_set():
        try:
            payload = {"timeout": 0, "allowed_updates": json.dumps(
                ["callback_query", "message"])}
            if offset is not None:
                payload["offset"] = offset
            data = _api_post("getUpdates", payload, retries=1)
            for upd in (data or {}).get("result", []):
                offset = upd["update_id"] + 1
                _write_offset(offset)
                try:
                    if "callback_query" in upd:
                        cq = upd["callback_query"]
                        _dispatch_callback(cq.get("data", ""), cq.get("id", ""))
                    elif "message" in upd:
                        _dispatch_text(upd["message"].get("text", ""))
                except Exception as exc:  # one bad update mustn't kill the loop
                    print(f"⚠️  [Telegram] Update handling error: {exc}")
        except Exception as exc:
            print(f"⚠️  [Telegram] Listener poll error: {exc}")
        _listener_stop.wait(poll)


def start_approval_listener() -> Optional[threading.Thread]:
    """Start the approval listener in a daemon thread (idempotent)."""
    global _listener_thread
    if not is_configured():
        print("⚠️  [Telegram] Not configured — approval listener not started.")
        return None
    if _listener_thread and _listener_thread.is_alive():
        return _listener_thread
    _listener_stop.clear()
    _listener_thread = threading.Thread(
        target=_listen_loop, name="tg-approval-listener", daemon=True)
    _listener_thread.start()
    print("👂 Approval listener active")
    return _listener_thread


def stop_approval_listener() -> None:
    """Signal the approval listener to stop."""
    _listener_stop.set()
