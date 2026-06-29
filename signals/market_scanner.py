"""
Market scanner (Phase 3).

The core scan loop: for every PASSED strategy × configured coin × timeframe it
fetches fresh data, analyses market context, checks whether the strategy fires,
applies confluence + confidence rules, prevents duplicates, persists signals,
and sends Telegram alerts. Scan-and-alert ONLY — never trades.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import pandas as pd

from backtesting import data_fetcher
from models.signal import Signal
from signals import signal_store, timeframe_minutes
from signals.market_analyzer import analyze_market
from signals.signal_detector import check_signal, compute_confidence
from storage import strategy_store
from utils.helpers import load_config, utc_now_str

try:
    from rich.console import Console
    from rich.panel import Panel

    _console = Console()
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _HAS_RICH = False


def _symbol(asset: str) -> str:
    """Normalise 'BTC' → 'BTC/USDT'; pass through full symbols."""
    asset = (asset or "").strip().upper()
    return asset if "/" in asset else f"{asset}/USDT"


def _passed_strategies() -> list:
    """Return all strategies whose latest backtest verdict is PASS."""
    return [
        c for c in strategy_store.list_cards()
        if isinstance(c.backtest_result, dict)
        and c.backtest_result.get("verdict") == "PASS"
    ]


def run_scan() -> dict[str, Any]:
    """Run one full scan across all strategies/coins/timeframes.

    Returns a summary dict and prints a scan-summary box.
    """
    config = load_config()
    assets = config.get("default_assets", ["BTC", "ETH", "SOL", "BNB"])
    timeframes = config.get("default_timeframes", ["1H", "4H", "1D"])
    min_conf = float(config.get("min_confidence_to_alert", 60))
    expiry_candles = int(config.get("signal_expiry_candles", 3))
    max_signals = int(config.get("max_signals_per_scan", 10))
    interval = int(config.get("scan_interval_minutes", 15))
    coin_delay = float(config.get("scan_delay_between_coins_seconds", 0.25))
    observation_ids = set(config.get("observation_mode_strategies", []))

    strategies = _passed_strategies()

    summary = {
        "timestamp": utc_now_str(), "active_strategies": len(strategies),
        "coins": len(assets), "timeframes": len(timeframes),
        "signals_found": 0, "alerts_sent": 0, "skipped_low_conf": 0,
        "skipped_duplicate": 0, "next_scan_minutes": interval,
    }

    if not strategies:
        print("ℹ️  [Scanner] No PASSED strategies to scan.")
        _print_summary(summary)
        return summary

    # --- Pass 1: fetch data + market context + regime per (asset, tf) --
    from signals.regime_classifier import classify_regime, record_regime

    contexts: dict[tuple, dict] = {}
    frames: dict[tuple, pd.DataFrame] = {}
    regimes: dict[tuple, dict] = {}
    for asset in assets:
        symbol = _symbol(asset)
        for tf in timeframes:
            # Per-coin isolation: any failure on one coin is logged and skipped,
            # never crashing the scan (critical for 50-coin 24/7 operation).
            try:
                df = data_fetcher.fetch_latest_ohlcv(symbol, tf.lower(), limit=200)
                if df is None or len(df) < 3:
                    print(f"⚠️  [Scanner] Insufficient data for {symbol} {tf}; "
                          f"skipping.")
                    continue
                frames[(asset, tf)] = df
                contexts[(asset, tf)] = analyze_market(df, asset)
                regime = classify_regime(df, asset)
                regimes[(asset, tf)] = regime
                record_regime(symbol, tf, regime)  # builds regime history
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️  [Scanner] {symbol} {tf} failed ({exc}); skipping.")
            finally:
                if coin_delay > 0:
                    time.sleep(coin_delay)  # respect Binance rate limits

    # --- Pass 2: detect signals per (asset, timeframe) -----------------
    alerts_budget = max_signals
    for asset in assets:
        symbol = _symbol(asset)
        for tf in timeframes:
            key = (asset, tf)
            if key not in frames:
                continue
            df, ctx = frames[key], contexts[key]
            regime = regimes.get(key, {})

            # --- Regime gate: skip ALL strategies in unsuitable regimes ---
            if regime and not regime.get("suitable_for_signals", True):
                print(f"⏭️  [Scanner] {symbol} {tf}: {regime.get('regime')} "
                      f"- skipping {len(strategies)} strategies")
                continue

            # Find every strategy firing here this scan (for confluence).
            fired: list[tuple] = []  # (card, detection)
            for card in strategies:
                if getattr(card, "engine", "generic") == "smc":
                    from signals.signal_detector import check_smc_signal
                    det = check_smc_signal(card, df, ctx)
                    # Deviation filter: downgrade LONGs in low-probability moves.
                    if det["fired"] and getattr(card, "uses_deviation_filter", False):
                        from smart_money.smc_engine import deviation_probability
                        if deviation_probability(df) == "LOW":
                            det["confidence"] = max(0, det["confidence"] - 15)
                            print(f"   ⚠️  {card.name}: low-probability deviation "
                                  f"→ confidence −15")
                else:
                    det = check_signal(card, df, ctx)
                if det["fired"]:
                    fired.append((card, det))

            for card, det in fired:
                summary["signals_found"] += 1

                # Market-condition filter: adapted strategies may require a
                # specific regime (originals default to 'any' → no effect).
                if card.market_condition in ("uptrend", "downtrend"):
                    want = "UPTREND" if card.market_condition == "uptrend" \
                        else "DOWNTREND"
                    if ctx.get("trend") != want:
                        print(f"⏭️  [Scanner] {card.name} {symbol} {tf}: market "
                              f"{ctx.get('trend')} ≠ required {want}; skipped.")
                        continue

                # --- Regime suitability for this signal direction (5.5) ----
                regime_delta = 0
                if regime:
                    ok_r, reason_r, regime_delta = _regime_ok(
                        regime, card, det["signal_type"])
                    if not ok_r:
                        print(f"⏭️  [Scanner] {card.name} {symbol} {tf}: {reason_r}")
                        continue

                # --- Performance best-conditions filter (5.5 Part 2) -------
                if not _conditions_ok(card.id, asset, tf, ctx):
                    print(f"⏭️  [Scanner] {card.name} {symbol} {tf}: outside "
                          f"its historically-best conditions.")
                    continue

                if det["confidence"] < min_conf:
                    summary["skipped_low_conf"] += 1
                    print(f"⏭️  [Scanner] {card.name} {symbol} {tf} below "
                          f"confidence ({det['confidence']} < {min_conf:.0f}).")
                    continue

                # --- Confluence: other strategies firing same direction ---
                agree = [c for c, d in fired
                         if d["signal_type"] == det["signal_type"]]
                confluence_names = [c.name for c in agree]
                confluence_count = len(agree)
                has_confluence = confluence_count >= 2

                # --- Timeframe alignment with a higher timeframe ---------
                aligned = _higher_tf_aligned(
                    asset, tf, det["signal_type"], timeframes, contexts)

                final_conf = compute_confidence(
                    det["signal_type"], ctx,
                    timeframe_aligned=aligned, has_confluence=has_confluence)
                # Volatile regimes reduce confidence.
                if regime_delta:
                    final_conf = max(0, min(100, final_conf + regime_delta))

                # --- Duplicate guard (never spam) ------------------------
                if signal_store.is_duplicate(card.id, symbol,
                                             det["signal_type"], timeframe=tf):
                    summary["skipped_duplicate"] += 1
                    print(f"🔁 [Scanner] Duplicate {card.name} {symbol} {tf} "
                          f"skipped.")
                    continue

                if alerts_budget <= 0:
                    print("🛑 [Scanner] Max signals per scan reached.")
                    break

                signal = _build_signal(
                    card, symbol, tf, det, ctx, confluence_count,
                    confluence_names, aligned, final_conf, expiry_candles)

                # --- Sentiment enhancement (keyword-based, zero LLM) ------
                # Adjusts signal.confidence_score in place; never blocks.
                sentiment = _check_sentiment(symbol, det["signal_type"], signal)

                # --- Trust score (Phase 5.5 Part 2) ----------------------
                trust = _trust_for(card.id)
                observation = card.id in observation_ids

                signal_store.save_signal(signal)

                if _send_alert(signal, sentiment, trust, regime, observation):
                    summary["alerts_sent"] += 1
                    alerts_budget -= 1

    _print_summary(summary)
    return summary


# --- Helpers -------------------------------------------------------------

def _higher_tf_aligned(asset: str, tf: str, signal_type: str,
                       timeframes: list[str], contexts: dict) -> bool:
    """True if the nearest higher timeframe's trend agrees with the signal."""
    cur_min = timeframe_minutes(tf)
    higher = sorted(
        (t for t in timeframes if timeframe_minutes(t) > cur_min),
        key=timeframe_minutes,
    )
    for ht in higher:
        ctx = contexts.get((asset, ht))
        if not ctx:
            continue
        trend = ctx.get("trend")
        return (signal_type == "BUY" and trend == "UPTREND") or \
               (signal_type == "SELL" and trend == "DOWNTREND")
    return False


def _build_signal(card, symbol, tf, det, ctx, confluence_count,
                  confluence_names, aligned, confidence, expiry_candles) -> Signal:
    """Assemble a full Signal object from detection + context."""
    now = utc_now_str()
    return Signal(
        strategy_id=card.id,
        strategy_name=f"{card.name} v{card.version}",
        asset=symbol,
        timeframe=tf,
        signal_type=det["signal_type"],
        entry_zone_low=det["entry_zone_low"],
        entry_zone_high=det["entry_zone_high"],
        current_price=det["current_price"],
        entry_price_at_signal=det["current_price"],
        confidence_score=confidence,
        market_trend=ctx.get("trend", "SIDEWAYS"),
        trend_strength=ctx.get("trend_strength", "WEAK"),
        volume_confirmation=bool(ctx.get("volume_confirmed", False)),
        confluence_count=confluence_count,
        confluence_strategies=confluence_names,
        source=card.source_url or card.source_type or "",
        timeframe_alignment=aligned,
        signal_status="ACTIVE",
        date_generated=now,
        date_expires=signal_store.compute_expiry(now, tf, expiry_candles),
        alerted=False,
    )


def _check_sentiment(symbol: str, signal_type: str, signal: Signal
                     ) -> Optional[dict[str, Any]]:
    """Run the X sentiment check and adjust the signal's confidence.

    Returns the sentiment dict (with an 'adjustment' delta) or None. Any
    failure is swallowed — sentiment must never block a signal alert.
    """
    config = load_config()
    if not config.get("sentiment_enabled", True):
        return None
    try:
        # Pure-Python keyword sentiment — ZERO LLM calls in the live scan.
        from sentiment.keyword_sentiment import check_sentiment_fast
        from sentiment.sentiment_analyzer import confidence_adjustment
        result = check_sentiment_fast(symbol, signal_type, signal_id=signal.id)
        if not result:
            return None
        delta = confidence_adjustment(
            result.get("overall_sentiment", "NEUTRAL"), signal_type)
        result = dict(result)
        result["adjustment"] = delta
        if delta:
            signal.confidence_score = max(
                0, min(100, signal.confidence_score + delta))
            print(f"🐦 [Scanner] Sentiment {result['overall_sentiment']} "
                  f"→ confidence {delta:+d} (now {signal.confidence_score}).")
        return result
    except Exception as exc:  # enhancement only — never block the alert
        print(f"⚠️  [Scanner] Sentiment check failed (skipping): {exc}")
        return None


def _regime_ok(regime: dict[str, Any], card, signal_type: str):
    """Wrap regime_classifier.is_regime_suitable (lazy import)."""
    from signals.regime_classifier import is_regime_suitable
    return is_regime_suitable(regime, card, signal_type)


def _conditions_ok(strategy_id: str, asset: str, tf: str,
                   ctx: dict[str, Any]) -> bool:
    """Check the strategy's historically-best conditions (None → no filter)."""
    from learning.performance_tracker import get_best_conditions
    best = get_best_conditions(strategy_id)
    if best is None:
        return True  # not enough data yet — don't restrict
    base = asset.split("/")[0].upper()
    if best.get("best_assets") and base not in best["best_assets"]:
        return False
    if best.get("best_timeframes") and \
            tf.upper() not in [t.upper() for t in best["best_timeframes"]]:
        return False
    if best.get("avoid_market") and ctx.get("trend") == best["avoid_market"]:
        return False
    if best.get("require_volume") and not ctx.get("volume_confirmed"):
        return False
    return True


def _trust_for(strategy_id: str) -> dict[str, Any]:
    """Build the trust dict (score + outcome count) for the alert."""
    from learning.performance_tracker import get_trust_score
    from storage import database as db
    perf = db.get_performance(strategy_id)
    return {"score": get_trust_score(strategy_id),
            "outcomes": (perf or {}).get("total_outcomes", 0)}


def _send_alert(signal: Signal, sentiment: Optional[dict[str, Any]] = None,
                trust: Optional[dict[str, Any]] = None,
                regime: Optional[dict[str, Any]] = None,
                observation: bool = False) -> bool:
    """Send the Telegram alert and mark the signal as alerted on success."""
    try:
        from alerts.telegram_alert import send_signal_alert
    except Exception as exc:
        print(f"⚠️  [Scanner] Alert module unavailable: {exc}")
        return False

    ok = send_signal_alert(signal, sentiment=sentiment, trust=trust,
                           regime=regime, observation=observation)
    if ok:
        signal.alerted = True
        signal.alert_sent_at = utc_now_str()
        signal_store.save_signal(signal)
    return ok


def _print_summary(s: dict[str, Any]) -> None:
    """Print the scan-summary box."""
    body = (
        f"{s['timestamp']} UTC\n"
        f"{'-' * 30}\n"
        f"Active strategies : {s['active_strategies']}\n"
        f"Coins scanned     : {s['coins']}\n"
        f"Timeframes        : {s['timeframes']}\n"
        f"Signals found     : {s['signals_found']}\n"
        f"Alerts sent       : {s['alerts_sent']}\n"
        f"Skipped (low conf): {s['skipped_low_conf']}\n"
        f"Skipped (dup)     : {s['skipped_duplicate']}\n"
        f"Next scan in      : {s['next_scan_minutes']} min"
    )
    if _HAS_RICH:
        _console.print(Panel(body, title="🔍 SCAN COMPLETE",
                             border_style="cyan", expand=False))
    else:
        line = "═" * 40
        print(f"\n╔{line}╗\n  🔍 SCAN COMPLETE\n╠{line}╣\n{body}\n╚{line}╝\n")
