"""
Backtest Report data model (Phase 2).

A Backtest Report captures the full result of running one Strategy Card against
historical data for a single asset/timeframe: summary statistics, a PASS/FAIL
verdict, and the per-trade log.

Uses Pydantic so the same object can be stored on the Strategy Card
(`backtest_result`), returned by the API, and printed by the report generator.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TradeRecord(BaseModel):
    """A single simulated trade (one entry + one exit)."""

    entry_time: str = Field(default="", description="ISO 'YYYY-MM-DD HH:MM'.")
    exit_time: str = Field(default="", description="ISO 'YYYY-MM-DD HH:MM'.")
    entry_price: float = 0.0
    exit_price: float = 0.0
    profit_usd: float = 0.0
    result: Literal["WIN", "LOSS"] = "LOSS"


class BacktestReport(BaseModel):
    """Full result of a single backtest run.

    Field layout mirrors the project's Backtest Report spec exactly so it can be
    serialised straight to the database / API without translation.
    """

    strategy_id: str = ""
    strategy_name: str = ""
    asset: str = ""
    timeframe: str = ""
    period_start: str = ""
    period_end: str = ""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    avg_profit_per_trade: float = 0.0
    avg_loss_per_trade: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    trade_size_usd: float = 100.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    total_signals_generated: int = 0

    verdict: Literal["PASS", "FAIL"] = "FAIL"
    verdict_reason: str = ""

    trades_log: list[TradeRecord] = Field(default_factory=list)

    date_run: str = ""
    pass_threshold: float = 50.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BacktestReport":
        """Build a report from a plain dict, ignoring unknown keys."""
        if not data:
            return cls()
        allowed = set(cls.model_fields.keys())
        clean = {k: v for k, v in data.items() if k in allowed}
        return cls(**clean)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation."""
        return self.model_dump()
