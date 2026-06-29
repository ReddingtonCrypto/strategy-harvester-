"""
Backtesting API endpoints (Phase 2).

Routes
------
POST /backtest/run            -> run a full backtest for a strategy
GET  /backtest/all            -> all saved backtest reports
GET  /backtest/passed         -> only strategies whose backtest PASSED
GET  /backtest/{strategy_id}  -> saved backtest report for one strategy

Note: the static '/all' and '/passed' routes are declared before the dynamic
'/{strategy_id}' route so they are matched correctly.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backtesting.backtest_runner import run_backtest
from storage import strategy_store

router = APIRouter(prefix="/backtest", tags=["backtesting"])


class RunBacktestRequest(BaseModel):
    """Request body for POST /backtest/run."""

    strategy_id: str
    asset: str = "BTC/USDT"
    timeframe: str = "4h"


@router.post("/run")
def run(req: RunBacktestRequest) -> dict:
    """Run a full backtest for a strategy and return the report.

    This is a synchronous, potentially slow call (it may fetch ~12 months of
    candles from Binance on a cache miss).
    """
    card = strategy_store.get_card(req.strategy_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    try:
        report = run_backtest(card, req.asset, req.timeframe)
    except Exception as exc:  # surface the cause instead of a bare 500
        raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}")
    return report.to_dict()


@router.get("/all")
def all_reports() -> list[dict]:
    """Return every saved backtest report."""
    reports = []
    for card in strategy_store.list_cards():
        if card.backtest_result:
            reports.append(card.backtest_result)
    return reports


@router.get("/passed")
def passed_strategies() -> list[dict]:
    """Return strategies whose latest backtest verdict is PASS."""
    out = []
    for card in strategy_store.list_cards():
        result = card.backtest_result
        if isinstance(result, dict) and result.get("verdict") == "PASS":
            out.append({
                "strategy_id": card.id,
                "strategy_name": card.name,
                "report": result,
            })
    return out


@router.get("/{strategy_id}")
def get_report(strategy_id: str) -> dict:
    """Return the saved backtest report for a strategy (404 if none)."""
    card = strategy_store.get_card(strategy_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    if not card.backtest_result:
        raise HTTPException(status_code=404, detail="No backtest report yet.")
    return card.backtest_result
