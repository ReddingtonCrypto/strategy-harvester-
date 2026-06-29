"""
Strategy CRUD endpoints.

Routes
------
GET   /strategies            -> list all Strategy Cards
GET   /strategies/{id}       -> get one Strategy Card
PATCH /strategies/{id}/approve -> approve a strategy
PATCH /strategies/{id}/reject  -> reject a strategy
POST  /strategies/add        -> add a new strategy manually
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.strategy_card import StrategyCard
from storage import strategy_store

router = APIRouter(prefix="/strategies", tags=["strategies"])


class NewStrategy(BaseModel):
    """Request body for manually adding a strategy.

    All fields are optional except `name`; the Strategy Card model fills in
    sensible defaults (id, date, status) for anything omitted.
    """

    name: str
    source_type: str = "manual"
    source_url: str = ""
    raw_content: str = ""
    indicators: list[str] = []
    entry_conditions: list[str] = []
    exit_conditions: list[str] = []
    timeframe: str = ""
    assets: list[str] = []
    market_condition: str = "any"
    confidence_score: int = 0


@router.get("")
def list_strategies() -> list[dict]:
    """Return all Strategy Cards."""
    return [c.to_dict() for c in strategy_store.list_cards()]


@router.get("/{strategy_id}")
def get_strategy(strategy_id: str) -> dict:
    """Return a single Strategy Card by id (404 if not found)."""
    card = strategy_store.get_card(strategy_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return card.to_dict()


@router.post("/add", status_code=201)
def add_strategy(payload: NewStrategy) -> dict:
    """Create and persist a new Strategy Card from request data."""
    card = StrategyCard.from_dict(payload.model_dump())
    strategy_store.save_card(card)
    return card.to_dict()


@router.patch("/{strategy_id}/approve")
def approve_strategy(strategy_id: str) -> dict:
    """Approve a Strategy Card (404 if not found)."""
    if not strategy_store.approve_card(strategy_id):
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return {"id": strategy_id, "status": "approved", "approved": True}


@router.patch("/{strategy_id}/reject")
def reject_strategy(strategy_id: str) -> dict:
    """Reject a Strategy Card (404 if not found)."""
    if not strategy_store.reject_card(strategy_id):
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return {"id": strategy_id, "status": "rejected", "approved": False}
