"""
Signal detector (Phase 3).

Decides whether a strategy's rule is firing on the latest CLOSED candle of live
data. Reuses the Phase 2 indicator engine and rule parser (no rewrites).

A fired entry rule → BUY; a fired exit rule (when entry didn't) → SELL.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from backtesting import indicator_engine
from backtesting.rule_parser import parse_strategy_rules, validate_expression
from models.strategy_card import StrategyCard
from utils.helpers import load_config

# Restricted namespace for evaluating rule expressions.
_SAFE_GLOBALS = {"__builtins__": {}}


def _safe_eval(code, row: pd.Series, prev: pd.Series) -> bool:
    """Evaluate a compiled rule expression for one candle; False on any error."""
    try:
        return bool(eval(code, _SAFE_GLOBALS, {"df": row, "prev": prev}))
    except Exception:
        return False


def compute_confidence(
    signal_type: str,
    market_context: dict[str, Any],
    *,
    timeframe_aligned: bool = False,
    has_confluence: bool = False,
) -> int:
    """Confidence score (0-100) for a fired signal.

    Base 50 (strategy passed backtest) plus context bonuses, per spec.
    """
    config = load_config()
    adx_strong = float(config.get("adx_strong_trend_threshold", 25))

    score = 50
    trend = market_context.get("trend")
    matches = (signal_type == "BUY" and trend == "UPTREND") or \
              (signal_type == "SELL" and trend == "DOWNTREND")
    if matches:
        score += 10
    if market_context.get("volume_confirmed"):
        score += 10
    if float(market_context.get("adx_value", 0)) > adx_strong:
        score += 10
    if timeframe_aligned:
        score += 10
    if has_confluence:
        score += 10
    return min(score, 100)


def check_signal(
    strategy_card: StrategyCard,
    live_df: pd.DataFrame,
    market_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Check whether `strategy_card` fires on the latest closed candle.

    Returns a dict: fired, signal_type, current_price, entry_zone_low,
    entry_zone_high, confidence. `fired` is False when nothing triggers or the
    strategy can't be evaluated.
    """
    not_fired = {"fired": False, "signal_type": None, "current_price": 0.0,
                 "entry_zone_low": 0.0, "entry_zone_high": 0.0, "confidence": 0}

    if live_df is None or len(live_df) < 3:
        return not_fired

    # --- Resolve rules (cached on the card when available) -------------
    rules = parse_strategy_rules(strategy_card)
    entry_rule, exit_rule = rules.get("entry_rule", ""), rules.get("exit_rule", "")
    if not entry_rule:
        print(f"⏭️  [Detector] '{strategy_card.name}' has no entry_rule — skipped.")
        return not_fired

    # --- Indicators on live data (reuse Phase 2 engine) ----------------
    df = indicator_engine.calculate_indicators(live_df, strategy_card.indicators)

    # Latest CLOSED candle is the second-to-last row (last row is forming).
    cur = df.iloc[-2]
    prev = df.iloc[-3]

    entry_code = _compile(entry_rule)
    exit_code = _compile(exit_rule) if exit_rule else None

    entry_fired = entry_code is not None and _safe_eval(entry_code, cur, prev)
    exit_fired = exit_code is not None and _safe_eval(exit_code, cur, prev)

    if entry_fired:
        signal_type = "BUY"
    elif exit_fired:
        signal_type = "SELL"
    else:
        return not_fired

    current_price = float(cur["close"])
    config = load_config()
    buffer = float(config.get("entry_zone_buffer_percent", 0.2)) / 100.0
    market_context = market_context or {}

    confidence = compute_confidence(signal_type, market_context)

    return {
        "fired": True,
        "signal_type": signal_type,
        "current_price": current_price,
        "entry_zone_low": round(current_price * (1 - buffer), 8),
        "entry_zone_high": round(current_price * (1 + buffer), 8),
        "confidence": confidence,
    }


def check_smc_signal(strategy_card: StrategyCard, live_df: pd.DataFrame,
                     market_context: Optional[dict[str, Any]] = None
                     ) -> dict[str, Any]:
    """Detect a Smart-Money entry on the latest closed candle (Phase 6).

    Returns the same dict shape as `check_signal`. SMC entries are LONG → BUY.
    """
    not_fired = {"fired": False, "signal_type": None, "current_price": 0.0,
                 "entry_zone_low": 0.0, "entry_zone_high": 0.0, "confidence": 0}
    if live_df is None or len(live_df) < 3:
        return not_fired

    from smart_money import smc_engine
    sig = smc_engine.latest_entry(live_df, strategy_card.engine_signal)
    if not sig:
        return not_fired

    price = float(sig["entry"])
    config = load_config()
    buffer = float(config.get("entry_zone_buffer_percent", 0.2)) / 100.0
    confidence = compute_confidence("BUY", market_context or {})
    return {
        "fired": True, "signal_type": "BUY", "current_price": price,
        "entry_zone_low": round(price * (1 - buffer), 8),
        "entry_zone_high": round(price * (1 + buffer), 8),
        "confidence": confidence,
        # Mechanical exits — let outcome tracking score WIN/LOSS by which is hit
        # first (same as the backtest), not by an arbitrary % move.
        "target": float(sig.get("target")) if sig.get("target") else 0.0,
        "stop": float(sig.get("stop")) if sig.get("stop") else 0.0,
    }


def _compile(expr: str):
    """Validate then compile a rule expression; return code obj or None."""
    ok, _ = validate_expression(expr)
    if not ok:
        return None
    try:
        return compile(expr, "<rule>", "eval")
    except SyntaxError:
        return None
