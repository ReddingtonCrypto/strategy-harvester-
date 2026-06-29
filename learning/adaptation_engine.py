"""
Adaptation engine (Phase 4).

Orchestrates a full learning run for one strategy: analyse outcomes → find
patterns (Claude) → build an improvement suggestion → persist it → send a
Telegram approval request with inline buttons. Never modifies the original
strategy and never auto-applies a suggestion.
"""

from __future__ import annotations

from typing import Any, Optional

from learning import outcome_analyzer, pattern_finder
from storage import database as db
from storage import strategy_store
from utils.helpers import generate_id, load_config, today_str, utc_now_str


def generate_adaptation(strategy_id: str, *, send_telegram: bool = True
                        ) -> Optional[dict[str, Any]]:
    """Run the full learning pipeline for a strategy.

    Returns the saved suggestion dict, or None when there isn't enough data or
    the projected improvement is below the configured threshold.
    """
    config = load_config()
    min_improvement = float(config.get("min_improvement_to_suggest", 5.0))

    card = strategy_store.get_card(strategy_id)
    if card is None:
        print(f"⚠️  [Adaptation] Strategy {strategy_id} not found.")
        return None

    # 1) Analyse outcomes.
    breakdown = outcome_analyzer.analyze_strategy_outcomes(strategy_id)
    if breakdown is None:
        return None  # insufficient data (analyzer already explained)

    # 2) Find patterns / get suggestion (saves a learning insight).
    result = pattern_finder.find_patterns(strategy_id, breakdown)

    current = float(breakdown.get("overall_win_rate", 0.0))
    projected = float(result.get("projected_win_rate", current))
    improvement = round(projected - current, 1)

    if improvement < min_improvement:
        print(f"ℹ️  [Adaptation] Projected improvement {improvement}% < "
              f"{min_improvement}% threshold — no suggestion created.")
        return None

    # 3) Build + persist the suggestion (PENDING).
    suggestion = {
        "id": generate_id(),
        "parent_strategy_id": strategy_id,
        "parent_strategy_name": f"{card.name} v{card.version}",
        "suggestion_date": today_str(),
        "suggested_changes": result.get("suggested_changes", {}),
        "reasoning": result.get("reasoning", ""),
        "projected_win_rate": projected,
        "current_win_rate": current,
        "improvement_pct": improvement,
        "status": "PENDING",
        "telegram_message_id": None,
        "reviewed_at": None,
        "new_strategy_id": None,
        "created_at": utc_now_str(),
    }
    suggestion["_key_findings"] = result.get("key_findings", [])  # for the message
    db.save_suggestion(suggestion)
    print(f"💡 [Adaptation] Suggestion {suggestion['id']} created "
          f"(+{improvement}% projected).")

    # 4) Send Telegram approval request.
    if send_telegram:
        message_id = send_approval_request(suggestion, card.version,
                                           result.get("key_findings", []))
        if message_id:
            db.update_suggestion(suggestion["id"],
                                 {"telegram_message_id": str(message_id)})
            suggestion["telegram_message_id"] = str(message_id)

    return suggestion


def format_changes(changes: dict[str, Any]) -> list[str]:
    """Turn a suggested_changes dict into human-readable bullet lines."""
    lines: list[str] = []
    params = changes.get("parameter_adjustments") or {}
    for name, val in params.items():
        if isinstance(val, dict) and "from" in val and "to" in val:
            lines.append(f"{name}: {val['from']} → {val['to']}")
        else:
            lines.append(f"{name}: {val}")
    for f in changes.get("add_filters") or []:
        lines.append(f"Add filter: {f}")
    for f in changes.get("remove_filters") or []:
        lines.append(f"Remove: {f}")
    assets = changes.get("asset_focus") or []
    if assets:
        lines.append(f"Focus assets: {', '.join(map(str, assets))}")
    tfs = changes.get("timeframe_focus") or []
    if tfs:
        lines.append(f"Focus timeframe: {', '.join(map(str, tfs))}")
    market = changes.get("market_condition_filter") or []
    if market:
        lines.append(f"Market filter: {', '.join(map(str, market))} only")
    return lines or ["(no concrete changes proposed)"]


def send_approval_request(suggestion: dict[str, Any], version: int = 1,
                          key_findings: Optional[list] = None) -> Optional[str]:
    """Send the Telegram approval message with inline APPROVE/REJECT/VIEW buttons."""
    from alerts import telegram_alert

    sid = suggestion["id"]
    parent_id = suggestion["parent_strategy_id"]
    key_findings = key_findings or suggestion.get("_key_findings", [])

    findings_block = "\n".join(f"- {f}" for f in key_findings) or "- (none)"
    changes_block = "\n".join(
        f"- {c}" for c in format_changes(suggestion.get("suggested_changes", {})))

    text = "\n".join([
        "💡 STRATEGY IMPROVEMENT FOUND",
        "",
        f"📊 Strategy: {suggestion['parent_strategy_name']}",
        f"📅 Analysis: based on {suggestion.get('_signals', '')}recent outcomes",
        "",
        f"📈 Current Win Rate  : {suggestion['current_win_rate']}%",
        f"🎯 Projected Win Rate: {suggestion['projected_win_rate']}%",
        f"📊 Improvement       : +{suggestion['improvement_pct']}%",
        "",
        "🔍 Key Findings:",
        findings_block,
        "",
        "✏️ Suggested Changes:",
        changes_block,
        "",
        "💭 Reasoning:",
        suggestion.get("reasoning", ""),
        "",
        "─────────────────────────────",
        f"Reply with:  APPROVE {sid}  /  REJECT {sid}",
    ])

    buttons = [[
        {"text": "✅ APPROVE", "callback_data": f"approve_{sid}"},
        {"text": "❌ REJECT", "callback_data": f"reject_{sid}"},
        {"text": "👁 ORIGINAL", "callback_data": f"view_{parent_id}"},
    ]]
    return telegram_alert.send_message_with_buttons(text, buttons)


# --- Daily adaptation pipeline (Phase 5.5, Part 5) ----------------------

def _passed_strategies() -> list:
    """Return all strategies whose latest backtest verdict is PASS."""
    return [c for c in strategy_store.list_cards()
            if isinstance(c.backtest_result, dict)
            and c.backtest_result.get("verdict") == "PASS"]


def _opt_change_desc(original: dict, optimal: dict) -> str:
    """Describe the parameter change between two param dicts."""
    for k, v in (optimal or {}).items():
        if (original or {}).get(k) != v:
            return f"{k.replace('_', ' ')} {original.get(k)}→{v}"
    return "params tuned"


def run_daily_adaptation(send_telegram: bool = True) -> dict[str, Any]:
    """Daily learning pipeline: performance → optimization → suggestions → report.

    Runs once per day (01:00 UTC via the scheduler). Claude is used only inside
    `generate_adaptation` (pattern finding); everything else is pure Python.
    """
    from signals import signal_store
    from learning import parameter_optimizer, performance_tracker

    config = load_config()
    opt_enabled = config.get("optimization_enabled", True)
    min_opt = int(config.get("min_signals_for_optimization", 5))
    min_improve = float(config.get("min_improvement_to_suggest", 3.0))

    cards = _passed_strategies()
    perf_updates: list[dict] = []
    optimizations: list[dict] = []
    suggestions: list[dict] = []

    for card in cards:
        old = db.get_performance(card.id)
        new = performance_tracker.update_performance(card.id)
        if new:
            perf_updates.append({
                "name": f"{card.name} v{card.version}",
                "old": (old or {}).get("win_rate_overall"),
                "new": new["win_rate_overall"],
                "outcomes": new["total_outcomes"],
            })

        if opt_enabled and new and new["total_outcomes"] >= min_opt:
            try:
                best = parameter_optimizer.run_full_optimization(card.id)
                if best and best["improvement"] >= min_improve:
                    optimizations.append({
                        "name": f"{card.name} v{card.version}",
                        "change": _opt_change_desc(best["original_params"],
                                                   best["optimal_params"]),
                        "improvement": best["improvement"],
                    })
            except Exception as exc:
                print(f"⚠️  [DailyAdaptation] Optimize failed for "
                      f"{card.name}: {exc}")

        try:
            sug = generate_adaptation(card.id, send_telegram=False)
            if sug:
                suggestions.append(sug)
        except Exception as exc:
            print(f"⚠️  [DailyAdaptation] Suggest failed for {card.name}: {exc}")

    today_sigs = signal_store.get_signals_today()
    decided = [s for s in today_sigs if s.outcome_result in ("WIN", "LOSS")]
    summary = {
        "signals_analyzed": len(today_sigs),
        "outcomes_recorded": len(decided),
        "strategies_updated": len(perf_updates),
        "optimizations": optimizations,
        "performance_updates": perf_updates,
        "suggestions": len(suggestions),
    }

    if send_telegram:
        _send_daily_report(summary)
    print(f"📚 [DailyAdaptation] Complete — {len(perf_updates)} updated, "
          f"{len(optimizations)} optimizations, {len(suggestions)} suggestions.")
    return summary


def _send_daily_report(summary: dict[str, Any]) -> Optional[str]:
    """Send the daily learning report with APPROVE ALL / REVIEW buttons."""
    from alerts import telegram_alert

    lines = [
        f"📚 DAILY LEARNING REPORT — {today_str()}",
        "",
        f"Signals analyzed  : {summary['signals_analyzed']}",
        f"Outcomes recorded : {summary['outcomes_recorded']}",
        f"Strategies updated: {summary['strategies_updated']}",
    ]
    if summary["optimizations"]:
        lines += ["", "🔧 Optimizations found:"]
        for o in summary["optimizations"]:
            lines.append(f"- {o['name']}: {o['change']} (+{o['improvement']}% win rate)")
    if summary["performance_updates"]:
        lines += ["", "📊 Performance updates:"]
        for p in summary["performance_updates"]:
            old = f"{p['old']}%" if p["old"] is not None else "—"
            lines.append(f"- {p['name']}: {old} → {p['new']}% "
                         f"({p['outcomes']} outcomes)")
    lines += ["", f"💡 Suggestions ready: {summary['suggestions']}"]

    if not telegram_alert.is_configured():
        print("\n".join(lines))
        return None

    if summary["suggestions"] > 0:
        lines.append("   Review in menu option 15 or tap buttons below 👇")
        buttons = [[
            {"text": "✅ APPROVE ALL", "callback_data": "approve_all"},
            {"text": "👁 REVIEW ONE BY ONE", "callback_data": "review_each"},
        ]]
        return telegram_alert.send_message_with_buttons("\n".join(lines), buttons)
    return telegram_alert.send_message("\n".join(lines))


def approve_all_pending() -> int:
    """Approve every PENDING suggestion (creates a version each). Returns count."""
    from learning import version_manager

    created = 0
    for sug in db.list_suggestions("PENDING"):
        if version_manager.create_adapted_version(sug["id"]):
            created += 1
    print(f"✅ [DailyAdaptation] Approved all — {created} versions created.")
    return created


def send_individual_reviews() -> int:
    """Re-send each PENDING suggestion as its own approval message."""
    from storage import strategy_store

    count = 0
    for sug in db.list_suggestions("PENDING"):
        card = strategy_store.get_card(sug["parent_strategy_id"])
        version = card.version if card else 1
        send_approval_request(sug, version, sug.get("_key_findings", []))
        count += 1
    return count
