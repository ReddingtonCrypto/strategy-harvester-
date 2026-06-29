"""
Strategy Card data model.

A Strategy Card is the core unit of the system: a structured, validated
representation of a single trading strategy extracted from some source.

We use Pydantic for validation and (de)serialisation so the same model can be
shared between the storage layer, the extractor, and the FastAPI endpoints.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Allowed values kept as type aliases for readability and validation.
SourceType = Literal["youtube", "telegram", "manual", "image_input", "twitter",
                     "local_video", "local_audio"]
MarketCondition = Literal["uptrend", "downtrend", "any"]
# 'pending_backtest' (Phase 4): an adapted version awaiting a fresh backtest.
Status = Literal["pending_review", "approved", "rejected", "pending_backtest"]


def _import_helpers():
    """Import id/date helpers lazily to avoid a hard import cycle."""
    from utils.helpers import generate_id, today_str

    return generate_id, today_str


class StrategyCard(BaseModel):
    """A single, validated trading-strategy record.

    Field layout mirrors the project's Strategy Card spec exactly so it can be
    serialised to/from the database and the API without translation.
    """

    id: str = Field(default="", description="Unique id (auto-generated if empty).")
    name: str = Field(default="Untitled Strategy", description="Human-readable name.")
    source_type: SourceType = Field(default="manual", description="Where it came from.")
    source_url: str = Field(default="", description="Link or channel/source name.")
    raw_content: str = Field(default="", description="Original extracted text.")

    indicators: list[str] = Field(default_factory=list)
    entry_conditions: list[str] = Field(default_factory=list)
    exit_conditions: list[str] = Field(default_factory=list)

    timeframe: str = Field(default="", description="e.g. '4H', '1D'.")
    assets: list[str] = Field(default_factory=list, description="e.g. ['BTC','ETH'].")
    # Free-form to allow descriptive conditions (e.g. 'ranging', 'reversal after
    # liquidity sweep'). The scanner only filters on exact 'uptrend'/'downtrend'.
    market_condition: str = Field(default="any")

    confidence_score: int = Field(default=0, ge=0, le=100)
    status: Status = Field(default="pending_review")
    date_added: str = Field(default="", description="ISO date 'YYYY-MM-DD'.")
    backtest_result: Optional[Any] = Field(default=None)
    approved: bool = Field(default=False)

    # --- Phase 2: parsed, machine-testable rules -------------------------
    # Populated by backtesting/rule_parser.py (plain-English conditions ->
    # Python boolean expressions). Cached here so a strategy only needs to be
    # parsed once.
    entry_rule: str = Field(default="", description="Python bool expr for entry.")
    exit_rule: str = Field(default="", description="Python bool expr for exit.")
    rule_notes: str = Field(default="", description="Assumptions made when parsing.")

    # --- Phase 3: strategy versioning (foundation for Phase 4 adaptation) -
    # Strategies are never mutated in place; an adaptation creates a new card
    # with an incremented version that points back to its parent.
    version: int = Field(default=1, description="Version number (starts at 1).")
    parent_id: Optional[str] = Field(default=None, description="Id of parent version.")
    version_notes: str = Field(default="", description="What changed this version.")
    is_adapted: bool = Field(default=False, description="True if AI-adapted.")
    adaptation_history: list[Any] = Field(default_factory=list)

    # --- Phase 6: Smart-Money engine routing -----------------------------
    # engine='smc' routes backtest/live signals to smart_money.smc_engine
    # (engine_signal in {'range','crt','textbook','filter'}) instead of the
    # generic indicator-rule eval path. 'generic' = the Phase 2 path.
    engine: str = Field(default="generic")
    engine_signal: str = Field(default="")
    uses_deviation_filter: bool = Field(default=False)

    def model_post_init(self, __context: Any) -> None:
        """Fill in auto-generated id and date if the caller left them blank."""
        generate_id, today_str = _import_helpers()
        if not self.id:
            self.id = generate_id()
        if not self.date_added:
            self.date_added = today_str()

    # ----- Convenience (de)serialisation helpers -------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyCard":
        """Build a StrategyCard from a plain dict, ignoring unknown keys.

        None values are dropped so that columns added by a later migration
        (NULL on older rows) fall back to their field defaults rather than
        failing validation.
        """
        allowed = set(cls.model_fields.keys())
        clean = {k: v for k, v in (data or {}).items()
                 if k in allowed and v is not None}
        return cls(**clean)

    @classmethod
    def from_extraction(
        cls,
        extracted: dict[str, Any],
        *,
        source_type: SourceType,
        source_url: str,
        raw_content: str,
    ) -> "StrategyCard":
        """Build a card from the LLM/manual extraction JSON plus source info.

        The extraction JSON only contains the analytical fields (name,
        indicators, conditions, etc.); source metadata is attached here.
        """
        data = dict(extracted or {})
        data.update(
            source_type=source_type,
            source_url=source_url,
            raw_content=raw_content,
        )
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation (JSON-serialisable)."""
        return self.model_dump()
