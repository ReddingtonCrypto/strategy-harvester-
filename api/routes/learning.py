"""
Learning + adaptation API endpoints (Phase 4).

Routes
------
GET  /learning/insights/{strategy_id}      -> latest learning insight
GET  /learning/suggestions                 -> all adaptation suggestions
GET  /learning/suggestions/pending         -> only pending suggestions
POST /learning/suggestions/{id}/approve    -> approve (creates a new version)
POST /learning/suggestions/{id}/reject     -> reject (archives the suggestion)
GET  /learning/lineage/{strategy_id}       -> full version tree
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from learning import version_manager
from storage import database as db

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/insights/{strategy_id}")
def latest_insight(strategy_id: str) -> dict:
    """Return the most recent learning insight for a strategy (404 if none)."""
    insight = db.get_latest_insight(strategy_id)
    if insight is None:
        raise HTTPException(status_code=404, detail="No insight for this strategy.")
    return insight


@router.get("/suggestions")
def all_suggestions() -> list[dict]:
    """Return all adaptation suggestions, newest first."""
    return db.list_suggestions()


@router.get("/suggestions/pending")
def pending_suggestions() -> list[dict]:
    """Return only PENDING adaptation suggestions."""
    return db.list_suggestions("PENDING")


@router.post("/suggestions/{suggestion_id}/approve")
def approve_suggestion(suggestion_id: str) -> dict:
    """Approve a suggestion, creating a new strategy version."""
    if db.get_suggestion(suggestion_id) is None:
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    card = version_manager.create_adapted_version(suggestion_id)
    if card is None:
        raise HTTPException(status_code=400,
                            detail="Could not create version (see server logs).")
    return {"approved": True, "new_strategy": card.to_dict()}


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: str) -> dict:
    """Reject a suggestion; the original strategy is untouched."""
    if db.get_suggestion(suggestion_id) is None:
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    version_manager.reject_suggestion(suggestion_id)
    return {"rejected": True, "suggestion_id": suggestion_id}


@router.get("/lineage/{strategy_id}")
def strategy_lineage(strategy_id: str) -> dict:
    """Return the full version tree for a strategy family."""
    return version_manager.get_strategy_lineage(strategy_id)


# --- Performance (Phase 5.5) --------------------------------------------

performance_router = APIRouter(prefix="/performance", tags=["performance"])


@performance_router.get("/all")
def performance_all() -> list[dict]:
    """All strategy performance stats."""
    return db.list_performance()


@performance_router.get("/rankings")
def performance_rankings() -> list[dict]:
    """Strategies ranked by trust score (highest first)."""
    rows = db.list_performance()
    return [{"strategy_id": r["strategy_id"], "strategy_name": r["strategy_name"],
             "version": r["version"], "trust_score": r["trust_score"],
             "win_rate_overall": r["win_rate_overall"],
             "total_outcomes": r["total_outcomes"]} for r in rows]


@performance_router.get("/{strategy_id}")
def performance_one(strategy_id: str) -> dict:
    """Performance stats for a specific strategy (404 if none)."""
    perf = db.get_performance(strategy_id)
    if perf is None:
        raise HTTPException(status_code=404, detail="No performance data.")
    return perf


# --- Regime (Phase 5.5) -------------------------------------------------

regime_router = APIRouter(prefix="/regime", tags=["regime"])


@regime_router.get("/history/{asset}")
def regime_history(asset: str) -> list[dict]:
    """Regime history for an asset (newest first)."""
    return db.get_regime_history(asset)


@regime_router.get("/{asset}/{timeframe}")
def current_regime(asset: str, timeframe: str) -> dict:
    """Current market regime for an asset/timeframe (live fetch)."""
    from backtesting import data_fetcher
    from signals.regime_classifier import classify_regime

    symbol = asset if "/" in asset else f"{asset.upper()}/USDT"
    df = data_fetcher.fetch_latest_ohlcv(symbol, timeframe.lower(), limit=200)
    if df is None or len(df) < 30:
        raise HTTPException(status_code=503,
                            detail="Could not fetch enough data for regime.")
    return classify_regime(df, asset)


# --- Optimization (Phase 5.5) -------------------------------------------

optimization_router = APIRouter(prefix="/optimization", tags=["optimization"])


@optimization_router.get("/{strategy_id}")
def latest_optimization(strategy_id: str) -> dict:
    """Latest parameter-optimization result for a strategy (404 if none)."""
    opt = db.get_latest_optimization(strategy_id)
    if opt is None:
        raise HTTPException(status_code=404, detail="No optimization yet.")
    return opt
