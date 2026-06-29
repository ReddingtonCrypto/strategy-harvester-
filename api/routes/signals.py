"""
Signal API endpoints (Phase 3).

Routes
------
GET /signals/active           -> all currently active signals
GET /signals/today            -> all signals generated today (UTC)
GET /signals/history?page=1   -> all signals, paginated (50/page)
GET /signals/summary          -> daily summary stats
GET /signals/{id}/outcome     -> outcome data for one signal
GET /signals/{asset}          -> all signals for an asset (e.g. BTC)

Static routes are declared before the dynamic '/{asset}' route so they match
correctly.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from signals import signal_store

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
def list_signals() -> list[dict]:
    """Convenience index: returns currently active signals."""
    return [s.to_dict() for s in signal_store.get_active_signals()]


@router.get("/active")
def active_signals() -> list[dict]:
    """All currently ACTIVE (non-expired) signals."""
    return [s.to_dict() for s in signal_store.get_active_signals()]


@router.get("/today")
def todays_signals() -> list[dict]:
    """All signals generated today (UTC)."""
    return [s.to_dict() for s in signal_store.get_signals_today()]


@router.get("/history")
def signal_history(page: int = Query(1, ge=1)) -> dict:
    """All signals ever, newest first, paginated 50 per page."""
    per_page = 50
    items = signal_store.get_history(page=page, per_page=per_page)
    return {
        "page": page,
        "per_page": per_page,
        "count": len(items),
        "signals": [s.to_dict() for s in items],
    }


@router.get("/summary")
def daily_summary() -> dict:
    """Today's signal summary stats (same data as the Telegram summary)."""
    return signal_store.get_daily_summary()


@router.get("/{signal_id}/outcome")
def signal_outcome(signal_id: str) -> dict:
    """Outcome data for a specific signal (404 if the signal is unknown)."""
    sig = signal_store.get_signal(signal_id)
    if sig is None:
        raise HTTPException(status_code=404, detail="Signal not found.")
    return {
        "id": sig.id,
        "asset": sig.asset,
        "signal_type": sig.signal_type,
        "entry_price_at_signal": sig.entry_price_at_signal,
        "outcome_1h": sig.outcome_1h,
        "outcome_4h": sig.outcome_4h,
        "outcome_24h": sig.outcome_24h,
        "outcome_result": sig.outcome_result,
        "outcome_pct_move": sig.outcome_pct_move,
    }


@router.get("/{asset}")
def signals_for_asset(asset: str) -> list[dict]:
    """All signals for a specific asset, e.g. /signals/BTC."""
    return [s.to_dict() for s in signal_store.get_signals_for_asset(asset)]
