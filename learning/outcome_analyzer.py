"""
Outcome analyzer (Phase 4).

Loads a strategy's signals with tracked outcomes and computes win-rate
breakdowns across asset, timeframe, market condition, volume confirmation,
trend strength, and confidence — the raw evidence the pattern finder hands to
Claude. Read-only: it never modifies signals or strategies.
"""

from __future__ import annotations

from typing import Any, Optional

from signals import signal_store
from utils.helpers import load_config

# Outcomes that count toward win-rate (NEUTRAL is excluded from the ratio).
_DECISIVE = {"WIN", "LOSS"}


def _win_rate(signals: list) -> dict[str, Any]:
    """Return win/loss/neutral counts and win rate for a list of signals."""
    wins = sum(1 for s in signals if s.outcome_result == "WIN")
    losses = sum(1 for s in signals if s.outcome_result == "LOSS")
    neutral = sum(1 for s in signals if s.outcome_result == "NEUTRAL")
    decided = wins + losses
    rate = round(wins / decided * 100, 1) if decided else 0.0
    return {"wins": wins, "losses": losses, "neutral": neutral,
            "total": len(signals), "win_rate": rate}


def _breakdown(signals: list, key) -> dict[str, dict]:
    """Group signals by `key(signal)` and compute win rate per group."""
    groups: dict[str, list] = {}
    for s in signals:
        k = key(s)
        if k is None or k == "":
            continue
        groups.setdefault(str(k), []).append(s)
    return {k: _win_rate(v) for k, v in groups.items()}


def _best_worst(breakdown: dict[str, dict], *, min_n: int = 1):
    """Return (best_key, worst_key) by win rate among groups with >= min_n decided."""
    eligible = {k: v for k, v in breakdown.items()
                if (v["wins"] + v["losses"]) >= min_n}
    if not eligible:
        return None, None
    best = max(eligible, key=lambda k: eligible[k]["win_rate"])
    worst = min(eligible, key=lambda k: eligible[k]["win_rate"])
    return best, worst


def _avg_confidence(signals: list, result: str) -> float:
    """Average confidence score of signals with a given outcome result."""
    vals = [s.confidence_score for s in signals if s.outcome_result == result]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def analyze_strategy_outcomes(strategy_id: str) -> Optional[dict[str, Any]]:
    """Analyse a strategy's signal outcomes.

    Returns a full breakdown dict, or None if there are fewer than
    `min_signals_for_analysis` decided outcomes (per config).
    """
    config = load_config()
    min_signals = int(config.get("min_signals_for_analysis", 10))

    # All signals for this strategy that have a recorded outcome.
    all_sigs = [s for s in _all_signals_for(strategy_id)
                if s.outcome_result in _DECISIVE | {"NEUTRAL"} and s.outcome_result]
    decided = [s for s in all_sigs if s.outcome_result in _DECISIVE]

    if len(decided) < min_signals:
        print(f"ℹ️  [Analyzer] Only {len(decided)} decided outcomes for "
              f"{strategy_id} (need {min_signals}). Skipping.")
        return None

    overall = _win_rate(all_sigs)

    by_asset = _breakdown(all_sigs, lambda s: s.asset.split("/")[0])
    by_tf = _breakdown(all_sigs, lambda s: s.timeframe)
    by_market = _breakdown(all_sigs, lambda s: s.market_trend)
    by_strength = _breakdown(all_sigs, lambda s: s.trend_strength)
    by_volume = _breakdown(
        all_sigs, lambda s: "confirmed" if s.volume_confirmation else "not_confirmed")
    by_confidence = _breakdown(all_sigs, _confidence_bucket)

    best_asset, _ = _best_worst(by_asset)
    best_tf, _ = _best_worst(by_tf)
    best_market, worst_market = _best_worst(by_market)

    vol_conf = by_volume.get("confirmed", {}).get("win_rate", 0.0)
    no_vol = by_volume.get("not_confirmed", {}).get("win_rate", 0.0)
    strong = by_strength.get("STRONG", {}).get("win_rate", 0.0)
    weak = by_strength.get("WEAK", {}).get("win_rate", 0.0)

    breakdown = {
        "strategy_id": strategy_id,
        "total_signals_analyzed": len(all_sigs),
        "decided_outcomes": len(decided),
        "overall_win_rate": overall["win_rate"],
        "overall": overall,
        "by_asset": by_asset,
        "by_timeframe": by_tf,
        "by_market_condition": by_market,
        "by_trend_strength": by_strength,
        "by_volume": by_volume,
        "by_confidence": by_confidence,
        "best_performing_asset": best_asset,
        "best_performing_timeframe": best_tf,
        "best_market_condition": best_market,
        "worst_market_condition": worst_market,
        "avg_confidence_winners": _avg_confidence(all_sigs, "WIN"),
        "avg_confidence_losers": _avg_confidence(all_sigs, "LOSS"),
        "volume_confirmed_win_rate": vol_conf,
        "no_volume_win_rate": no_vol,
        "strong_trend_win_rate": strong,
        "weak_trend_win_rate": weak,
        # Raw per-signal numbers for Claude (price move + confidence + context).
        "raw_signal_outcomes": [
            {"asset": s.asset, "timeframe": s.timeframe,
             "signal_type": s.signal_type, "confidence": s.confidence_score,
             "market_trend": s.market_trend, "trend_strength": s.trend_strength,
             "volume_confirmed": s.volume_confirmation,
             "pct_move": s.outcome_pct_move, "result": s.outcome_result}
            for s in all_sigs
        ],
        "note": ("Indicator-level snapshots (e.g. exact RSI at entry) are not "
                 "stored on signals yet; analysis uses context + outcome fields."),
    }
    print(f"✅ [Analyzer] {strategy_id}: {len(decided)} decided outcomes, "
          f"overall win rate {overall['win_rate']}%.")
    return breakdown


def _confidence_bucket(s) -> str:
    """Bucket a signal's confidence into 60-70 / 70-80 / 80-100."""
    c = s.confidence_score
    if c < 70:
        return "60-70"
    if c < 80:
        return "70-80"
    return "80-100"


def _all_signals_for(strategy_id: str) -> list:
    """All signals (any status) belonging to a strategy id."""
    # signal_store has no by-strategy helper; filter the full history.
    return [s for s in signal_store.get_history(per_page=100000)
            if s.strategy_id == strategy_id]
