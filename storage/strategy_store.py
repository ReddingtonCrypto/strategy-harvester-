"""
Strategy store — high-level interface over the database.

Works with `StrategyCard` objects (not raw dicts) and adds domain operations
like approve/reject and JSON export. The terminal menu and API both use this.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from models.strategy_card import StrategyCard
from storage import database as db
from utils.helpers import now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = PROJECT_ROOT / "exports"


def init() -> None:
    """Ensure the database schema exists. Call once at startup."""
    db.init_db()


def save_card(card: StrategyCard) -> StrategyCard:
    """Persist a Strategy Card and return it (with any generated fields)."""
    db.save(card.to_dict())
    print(f"💾 Saved strategy '{card.name}' (id={card.id}).")
    return card


def get_card(strategy_id: str) -> Optional[StrategyCard]:
    """Load a single Strategy Card by id, or None if it does not exist."""
    data = db.get(strategy_id)
    return StrategyCard.from_dict(data) if data else None


def list_cards() -> list[StrategyCard]:
    """Return all Strategy Cards as model objects."""
    return [StrategyCard.from_dict(d) for d in db.list_all()]


def update_card(strategy_id: str, fields: dict) -> bool:
    """Update arbitrary fields on a card. Returns True on success."""
    return db.update(strategy_id, fields)


def delete_card(strategy_id: str) -> bool:
    """Delete a card by id. Returns True if removed."""
    ok = db.delete(strategy_id)
    if ok:
        print(f"🗑️  Deleted strategy id={strategy_id}.")
    else:
        print(f"⚠️  No strategy found with id={strategy_id}.")
    return ok


def approve_card(strategy_id: str) -> bool:
    """Mark a strategy approved. Returns True on success."""
    ok = db.update(strategy_id, {"status": "approved", "approved": True})
    print("✅ Strategy approved." if ok else f"⚠️  id={strategy_id} not found.")
    return ok


def reject_card(strategy_id: str) -> bool:
    """Mark a strategy rejected. Returns True on success."""
    ok = db.update(strategy_id, {"status": "rejected", "approved": False})
    print("🚫 Strategy rejected." if ok else f"⚠️  id={strategy_id} not found.")
    return ok


def export_to_json(path: Optional[str] = None) -> str:
    """Export every Strategy Card to a JSON file.

    Returns the path of the written file. Defaults to
    exports/strategies_<timestamp>.json.
    """
    EXPORT_DIR.mkdir(exist_ok=True)
    if path is None:
        stamp = now_iso().replace(":", "-")
        path = str(EXPORT_DIR / f"strategies_{stamp}.json")

    cards = [c.to_dict() for c in list_cards()]
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cards, fh, indent=2, ensure_ascii=False)
        print(f"📤 Exported {len(cards)} strategies to {path}")
    except OSError as exc:
        print(f"❌ Failed to export strategies: {exc}")
        raise
    return path
