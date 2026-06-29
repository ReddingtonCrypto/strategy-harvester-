"""
Version manager (Phase 4).

Creates new *versioned* strategies when the user approves an adaptation
suggestion. The original strategy is read-only and keeps running; the new
version starts in `pending_backtest` status (approved=False) and must pass a
fresh backtest before it can go live.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from learning.adaptation_engine import format_changes
from models.strategy_card import StrategyCard
from storage import database as db
from storage import strategy_store
from utils.helpers import load_config, utc_now_str

# Map market-filter labels to the StrategyCard market_condition literal.
_MARKET_MAP = {"UPTREND": "uptrend", "DOWNTREND": "downtrend"}
# Known filter phrases that translate to an extra rule clause.
_FILTER_RULES = {"volume confirmed only": "df['volume'] > df['volume_sma_20']"}


def create_adapted_version(suggestion_id: str) -> Optional[StrategyCard]:
    """Create a new strategy version from an approved suggestion.

    Idempotent: if the suggestion already produced a version, returns it.
    """
    suggestion = db.get_suggestion(suggestion_id)
    if suggestion is None:
        print(f"⚠️  [VersionMgr] Suggestion {suggestion_id} not found.")
        return None

    if suggestion.get("status") == "APPROVED" and suggestion.get("new_strategy_id"):
        existing = strategy_store.get_card(suggestion["new_strategy_id"])
        if existing:
            print("ℹ️  [VersionMgr] Suggestion already approved; returning version.")
            return existing

    parent = strategy_store.get_card(suggestion["parent_strategy_id"])
    if parent is None:
        print("⚠️  [VersionMgr] Parent strategy not found.")
        return None

    # Enforce the per-strategy version cap.
    config = load_config()
    max_versions = int(config.get("max_versions_per_strategy", 10))
    lineage = get_strategy_lineage(parent.id)
    if lineage["total_versions"] >= max_versions:
        msg = (f"⚠️ Version limit ({max_versions}) reached for "
               f"{parent.name}; not creating a new version.")
        print(msg)
        _notify(msg)
        return None

    changes = suggestion.get("suggested_changes", {}) or {}
    new_card = _apply_changes(parent, changes)

    # Versioning metadata.
    new_card.version = parent.version + 1
    new_card.parent_id = parent.id
    new_card.is_adapted = True
    new_card.status = "pending_backtest"
    new_card.approved = False
    new_card.backtest_result = None  # must be re-tested before going live
    change_lines = format_changes(changes)
    new_card.version_notes = (
        f"v{new_card.version} adapted from v{parent.version}: "
        + "; ".join(change_lines)
    )
    history_entry = {
        "version": new_card.version,
        "from_version": parent.version,
        "date": utc_now_str(),
        "suggestion_id": suggestion_id,
        "changes": changes,
        "reasoning": suggestion.get("reasoning", ""),
    }
    new_card.adaptation_history = list(parent.adaptation_history) + [history_entry]

    strategy_store.save_card(new_card)

    # Mark the suggestion approved + link the new version.
    db.update_suggestion(suggestion_id, {
        "status": "APPROVED",
        "reviewed_at": utc_now_str(),
        "new_strategy_id": new_card.id,
    })

    _notify_created(parent, new_card, change_lines)
    print(f"✅ [VersionMgr] Created {new_card.name} v{new_card.version} "
          f"(id={new_card.id}).")
    return new_card


def reject_suggestion(suggestion_id: str) -> bool:
    """Mark a suggestion REJECTED and notify. The original is untouched."""
    suggestion = db.get_suggestion(suggestion_id)
    if suggestion is None:
        print(f"⚠️  [VersionMgr] Suggestion {suggestion_id} not found.")
        return False
    db.update_suggestion(suggestion_id, {
        "status": "REJECTED", "reviewed_at": utc_now_str(),
    })
    name = suggestion.get("parent_strategy_name", "the strategy")
    _notify(f"❌ Suggestion rejected. Original strategy {name} "
            f"continues unchanged.")
    print(f"🚫 [VersionMgr] Suggestion {suggestion_id} rejected.")
    return True


def get_strategy_lineage(strategy_id: str) -> dict[str, Any]:
    """Return the full version tree for a strategy family."""
    cards = strategy_store.list_cards()
    by_id = {c.id: c for c in cards}

    # Walk up to the root of this family.
    start = by_id.get(strategy_id)
    root = start
    seen = set()
    while root and root.parent_id and root.parent_id in by_id and root.id not in seen:
        seen.add(root.id)
        root = by_id[root.parent_id]

    if root is None:
        return {"original": None, "versions": [], "total_versions": 0,
                "best_performing_version": None}

    # Collect everyone whose family root is this root.
    def _root_of(card: StrategyCard) -> StrategyCard:
        cur, guard = card, set()
        while cur.parent_id and cur.parent_id in by_id and cur.id not in guard:
            guard.add(cur.id)
            cur = by_id[cur.parent_id]
        return cur

    family = [c for c in cards if _root_of(c).id == root.id]
    family.sort(key=lambda c: c.version)
    versions = [c for c in family if c.id != root.id]

    best = _best_version(family)

    return {
        "original": root.to_dict(),
        "versions": [c.to_dict() for c in versions],
        "total_versions": len(family),
        "best_performing_version": best,
    }


# --- Internals -----------------------------------------------------------

def _apply_changes(parent: StrategyCard, changes: dict[str, Any]) -> StrategyCard:
    """Return a NEW card copying the parent with suggested changes applied."""
    data = parent.to_dict()
    # Fresh identity / lineage handled by the caller.
    data["id"] = ""
    new = StrategyCard.from_dict(data)

    # Asset focus.
    assets = changes.get("asset_focus") or []
    if assets:
        new.assets = [str(a).split("/")[0].upper() for a in assets]

    # Timeframe focus.
    tfs = changes.get("timeframe_focus") or []
    if tfs and tfs[0]:
        new.timeframe = str(tfs[0])

    # Market-condition filter.
    market = changes.get("market_condition_filter") or []
    if market:
        new.market_condition = _MARKET_MAP.get(str(market[0]).upper(), "any")

    # Parameter adjustments → best-effort numeric replacement in the rule.
    params = changes.get("parameter_adjustments") or {}
    for _name, val in params.items():
        if isinstance(val, dict) and "from" in val and "to" in val:
            frm, to = str(val["from"]), str(val["to"])
            new.entry_rule = re.sub(rf"\b{re.escape(frm)}\b", to, new.entry_rule)
            new.exit_rule = re.sub(rf"\b{re.escape(frm)}\b", to, new.exit_rule)

    # Add-filters → append to conditions and (when known) to the entry rule.
    for f in changes.get("add_filters") or []:
        new.entry_conditions = list(new.entry_conditions) + [str(f)]
        clause = _FILTER_RULES.get(str(f).lower())
        if clause and clause not in new.entry_rule:
            new.entry_rule = f"({new.entry_rule}) and ({clause})" \
                if new.entry_rule else clause

    return new


def _best_version(family: list[StrategyCard]) -> Optional[str]:
    """Return a label for the best-performing version by backtest win rate."""
    best_card, best_rate = None, -1.0
    for c in family:
        if isinstance(c.backtest_result, dict):
            rate = c.backtest_result.get("win_rate")
            if isinstance(rate, (int, float)) and rate > best_rate:
                best_rate, best_card = rate, c
    if best_card is None:
        return None
    return f"v{best_card.version} (win rate {best_rate}%)"


def _notify_created(parent: StrategyCard, new: StrategyCard,
                    change_lines: list[str]) -> None:
    """Send the 'new version created' Telegram confirmation."""
    changes_block = "\n".join(f"- {c}" for c in change_lines)
    text = "\n".join([
        "✅ NEW STRATEGY VERSION CREATED",
        "",
        f"📊 {new.name} v{new.version} created from v{parent.version}",
        f"🔗 Parent: {parent.name} v{parent.version} (id: {parent.id})",
        "",
        "Changes applied:",
        changes_block,
        "",
        f"⚡ Next step: Run backtest on v{new.version}",
        f"   Menu option 6 → select {new.name} v{new.version}",
        "",
        "Both versions remain active:",
        f"- v{parent.version}: continues running (proven)",
        f"- v{new.version}: needs backtest before going live",
    ])
    _notify(text)


def _notify(text: str) -> None:
    """Send a Telegram message if configured (best-effort)."""
    try:
        from alerts import telegram_alert
        if telegram_alert.is_configured():
            telegram_alert.send_message(text)
    except Exception as exc:  # never let notification failure break the flow
        print(f"⚠️  [VersionMgr] Telegram notify failed: {exc}")
