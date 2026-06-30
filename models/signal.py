"""
Signal data model (Phase 3).

A Signal is a single live alert produced when a PASSED strategy's entry/exit
rule fires on the latest closed candle. It carries market context (trend,
volume, confluence), alert bookkeeping, and outcome-tracking fields that are
filled in later (1H / 4H / 24H after generation) to feed Phase 4 learning.

No autonomous trading — a Signal is informational only.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SignalType = Literal["BUY", "SELL"]
MarketTrend = Literal["UPTREND", "DOWNTREND", "SIDEWAYS"]
TrendStrength = Literal["STRONG", "MODERATE", "WEAK"]
SignalStatus = Literal["ACTIVE", "EXPIRED", "TRIGGERED"]
OutcomeResult = Literal["WIN", "LOSS", "NEUTRAL"]


def _gen_id() -> str:
    """Generate a unique id (imported lazily to avoid an import cycle)."""
    from utils.helpers import generate_id

    return generate_id()


class Signal(BaseModel):
    """A live trading signal / alert with outcome tracking."""

    id: str = Field(default="", description="Unique id (auto if blank).")
    strategy_id: str = ""
    strategy_name: str = ""
    asset: str = ""
    timeframe: str = ""
    signal_type: SignalType = "BUY"

    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    current_price: float = 0.0
    entry_price_at_signal: float = 0.0
    confidence_score: int = 0

    # Mechanical exits (SMC signals). When both are set, outcome tracking scores
    # WIN/LOSS by whichever is hit first — matching the backtest simulator —
    # instead of a fixed % move threshold. 0.0 → fall back to % thresholds.
    target_price: float = 0.0
    stop_price: float = 0.0

    market_trend: MarketTrend = "SIDEWAYS"
    trend_strength: TrendStrength = "WEAK"
    volume_confirmation: bool = False
    confluence_count: int = 1
    confluence_strategies: list[str] = Field(default_factory=list)

    source: str = ""
    timeframe_alignment: bool = False
    signal_status: SignalStatus = "ACTIVE"

    date_generated: str = Field(default="", description="UTC 'YYYY-MM-DD HH:MM:SS'.")
    date_expires: str = Field(default="", description="UTC 'YYYY-MM-DD HH:MM:SS'.")

    alerted: bool = False
    alert_sent_at: Optional[str] = None

    # Shadow mode: 'live' signals send Telegram alerts; 'shadow' signals are
    # logged + outcome-tracked only (untested strategy/timeframe combos we want
    # to observe in the dashboard without firing fake live alerts).
    mode: Literal["live", "shadow"] = "live"

    # Outcome tracking (filled by signal_store.update_signal_outcomes).
    outcome_1h: Optional[float] = None
    outcome_4h: Optional[float] = None
    outcome_24h: Optional[float] = None
    outcome_result: Optional[OutcomeResult] = None
    outcome_pct_move: Optional[float] = None

    def model_post_init(self, __context: Any) -> None:
        """Auto-generate an id if none was supplied."""
        if not self.id:
            self.id = _gen_id()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Signal":
        """Build a Signal from a plain dict, ignoring unknown keys.

        None values are dropped so columns added by a later migration (e.g.
        target_price/stop_price stored NULL on old rows) fall back to their
        field default instead of failing float validation.
        """
        allowed = set(cls.model_fields.keys())
        clean = {k: v for k, v in (data or {}).items()
                 if k in allowed and v is not None}
        return cls(**clean)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation."""
        return self.model_dump()
