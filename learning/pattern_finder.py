"""
Pattern finder (Phase 4).

Sends the outcome breakdown to Claude Opus 4.8 for deep pattern analysis and
returns a structured improvement suggestion. Persists a learning_insights row
(the permanent memory). If Claude is unavailable, a deterministic heuristic
fallback derives a suggestion from the same breakdown so the pipeline still
works offline.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from storage import database as db
from storage import strategy_store
from utils.helpers import extract_json, generate_id, get_env, today_str, utc_now_str

_MODEL = "claude-opus-4-8"

PROMPT_TEMPLATE = """\
You are a quantitative trading strategy analyst.
Analyze these signal outcomes for a trading strategy
and find patterns that separate winning signals
from losing signals.

Strategy: [STRATEGY_NAME]
Original Rules: [ENTRY_CONDITIONS] / [EXIT_CONDITIONS]

Outcome Data:
[BREAKDOWN_JSON]

Find:
1. Which conditions strongly correlate with WINS?
2. Which conditions strongly correlate with LOSSES?
3. What specific parameter changes would improve win rate?
4. Which assets/timeframes to focus on or avoid?
5. What market conditions to filter out?

Respond ONLY in this JSON format:
{
  "key_findings": [
    "Finding 1 with specific numbers",
    "Finding 2 with specific numbers"
  ],
  "suggested_changes": {
    "parameter_adjustments": {},
    "add_filters": [],
    "remove_filters": [],
    "asset_focus": [],
    "timeframe_focus": [],
    "market_condition_filter": []
  },
  "projected_win_rate": 0.0,
  "confidence_in_suggestion": 0,
  "reasoning": "detailed explanation"
}
"""


def find_patterns(strategy_id: str, insight_data: dict[str, Any]) -> dict[str, Any]:
    """Analyse the breakdown, persist a learning insight, and return a suggestion.

    The returned dict follows Claude's JSON schema (key_findings,
    suggested_changes, projected_win_rate, confidence_in_suggestion, reasoning).
    """
    card = strategy_store.get_card(strategy_id)
    name = card.name if card else strategy_id
    entry = "; ".join(card.entry_conditions) if card else ""
    exit_ = "; ".join(card.exit_conditions) if card else ""

    api_key = get_env("CLAUDE_API_KEY")
    if api_key:
        result = _call_claude(name, entry, exit_, insight_data, api_key)
        if result is None:
            print("⚠️  [PatternFinder] Claude failed — using heuristic fallback.")
            result = _fallback(insight_data)
    else:
        print("⚠️  [PatternFinder] No CLAUDE_API_KEY — using heuristic fallback.")
        result = _fallback(insight_data)

    _save_insight(strategy_id, name, insight_data, result)
    return result


# --- Claude path ---------------------------------------------------------

def _build_prompt(name: str, entry: str, exit_: str,
                  insight_data: dict[str, Any]) -> str:
    return (
        PROMPT_TEMPLATE
        .replace("[STRATEGY_NAME]", name)
        .replace("[ENTRY_CONDITIONS]", entry or "(none)")
        .replace("[EXIT_CONDITIONS]", exit_ or "(none)")
        .replace("[BREAKDOWN_JSON]", json.dumps(insight_data, default=str, indent=2))
    )


def _call_claude(name, entry, exit_, insight_data, api_key) -> Optional[dict]:
    """Call Claude Opus 4.8 (one retry) and return parsed JSON, or None."""
    try:
        import anthropic
    except ImportError:
        print("❌ [PatternFinder] 'anthropic' not installed.")
        return None

    prompt = _build_prompt(name, entry, exit_, insight_data)
    for attempt in (1, 2):
        try:
            print(f"🧠 [PatternFinder] Asking Claude (attempt {attempt})...")
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=_MODEL, max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in msg.content
                           if getattr(b, "type", None) == "text")
            parsed = extract_json(text)
            if parsed and "suggested_changes" in parsed:
                parsed.setdefault("reasoning", "")
                return parsed
        except Exception as exc:
            print(f"❌ [PatternFinder] Claude error: {exc}")
    return None


# --- Heuristic fallback --------------------------------------------------

def _fallback(d: dict[str, Any]) -> dict[str, Any]:
    """Derive a sensible suggestion from the breakdown without an LLM."""
    overall = d.get("overall_win_rate", 0.0)
    best_market = d.get("best_market_condition")
    worst_market = d.get("worst_market_condition")
    best_tf = d.get("best_performing_timeframe")

    by_asset = d.get("by_asset", {})
    asset_focus = [a for a, v in by_asset.items()
                   if v["win_rate"] >= overall and (v["wins"] + v["losses"]) >= 1]

    add_filters = []
    vol_conf = d.get("volume_confirmed_win_rate", 0.0)
    no_vol = d.get("no_volume_win_rate", 0.0)
    if vol_conf >= no_vol + 10:
        add_filters.append("volume confirmed only")
    if d.get("strong_trend_win_rate", 0.0) >= d.get("weak_trend_win_rate", 0.0) + 10:
        add_filters.append("strong trend only")

    market_filter = []
    by_market = d.get("by_market_condition", {})
    if best_market and worst_market and best_market != worst_market:
        bw = by_market.get(best_market, {}).get("win_rate", 0.0)
        ww = by_market.get(worst_market, {}).get("win_rate", 0.0)
        if bw >= ww + 10:
            market_filter = [best_market]

    # Projected = win rate of the best market subset (realistic upper bound).
    projected = by_market.get(best_market, {}).get("win_rate", overall) \
        if best_market else overall
    projected = round(max(projected, overall), 1)

    findings = [
        f"Overall win rate is {overall}% across "
        f"{d.get('total_signals_analyzed', 0)} signals.",
    ]
    if best_market and market_filter:
        findings.append(
            f"{best_market} win rate "
            f"{by_market.get(best_market, {}).get('win_rate')}% vs "
            f"{worst_market} {by_market.get(worst_market, {}).get('win_rate')}%.")
    if add_filters and "volume confirmed only" in add_filters:
        findings.append(
            f"Volume-confirmed win rate {vol_conf}% vs {no_vol}% without volume.")
    if asset_focus:
        findings.append(f"Best assets: {', '.join(asset_focus)}.")

    return {
        "key_findings": findings,
        "suggested_changes": {
            "parameter_adjustments": {},
            "add_filters": add_filters,
            "remove_filters": [],
            "asset_focus": asset_focus,
            "timeframe_focus": [best_tf] if best_tf else [],
            "market_condition_filter": market_filter,
        },
        "projected_win_rate": projected,
        "confidence_in_suggestion": 55,
        "reasoning": ("Heuristic analysis (no LLM): focus on the best-performing "
                      "market/assets and add filters where they clearly raised the "
                      "win rate in the historical outcomes."),
    }


# --- Persistence ---------------------------------------------------------

def _save_insight(strategy_id: str, name: str, breakdown: dict[str, Any],
                  result: dict[str, Any]) -> None:
    """Write a learning_insights row combining breakdown + Claude analysis."""
    pattern_summary = " | ".join(result.get("key_findings", [])) or \
        "No notable patterns."
    insight = {
        "id": generate_id(),
        "strategy_id": strategy_id,
        "strategy_name": name,
        "analysis_date": today_str(),
        "total_signals_analyzed": breakdown.get("total_signals_analyzed", 0),
        "overall_win_rate": breakdown.get("overall_win_rate", 0.0),
        "best_performing_asset": breakdown.get("best_performing_asset"),
        "best_performing_timeframe": breakdown.get("best_performing_timeframe"),
        "best_market_condition": breakdown.get("best_market_condition"),
        "worst_market_condition": breakdown.get("worst_market_condition"),
        "avg_confidence_winners": breakdown.get("avg_confidence_winners", 0.0),
        "avg_confidence_losers": breakdown.get("avg_confidence_losers", 0.0),
        "volume_confirmed_win_rate": breakdown.get("volume_confirmed_win_rate", 0.0),
        "no_volume_win_rate": breakdown.get("no_volume_win_rate", 0.0),
        "strong_trend_win_rate": breakdown.get("strong_trend_win_rate", 0.0),
        "weak_trend_win_rate": breakdown.get("weak_trend_win_rate", 0.0),
        "pattern_summary": pattern_summary,
        "raw_analysis": result.get("reasoning", ""),
        "full_breakdown": breakdown,
        "created_at": utc_now_str(),
    }
    db.save_insight(insight)
    print(f"💾 [PatternFinder] Saved learning insight for {name}.")
