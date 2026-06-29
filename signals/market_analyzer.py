"""
Market analyzer (Phase 3).

Provides market context (trend, strength, volume confirmation) so a good
strategy doesn't fire in the wrong conditions. Reuses the Phase 2 indicator
engine; reads the latest CLOSED candle only.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from backtesting import indicator_engine
from utils.helpers import load_config


def _closed_row(df: pd.DataFrame):
    """Return the latest CLOSED candle.

    Binance returns the in-progress candle as the final row, so the most recent
    *closed* candle is the second-to-last row when one is available.
    """
    if len(df) >= 2:
        return df.iloc[-2]
    return df.iloc[-1]


def _has_indicators(df: pd.DataFrame) -> bool:
    """True if the needed indicator columns are already present."""
    needed = {"ema_200", "adx_14", "volume_sma_20"}
    return needed.issubset(df.columns)


def analyze_market(df: pd.DataFrame, asset: str = "") -> dict[str, Any]:
    """Analyse market context for the latest closed candle.

    Returns: trend, trend_strength, volume_confirmed, above_ema200,
    adx_value, market_score.
    """
    config = load_config()
    adx_strong = float(config.get("adx_strong_trend_threshold", 25))
    adx_weak = float(config.get("adx_weak_trend_threshold", 15))
    vol_mult = float(config.get("volume_confirmation_multiplier", 1.5))

    # Sensible neutral default if we can't analyse.
    default = {
        "trend": "SIDEWAYS", "trend_strength": "WEAK", "volume_confirmed": False,
        "above_ema200": False, "adx_value": 0.0, "market_score": 0,
    }
    if df is None or df.empty:
        return default

    if not _has_indicators(df):
        df = indicator_engine.calculate_all_indicators(df)

    row = _closed_row(df)
    price = float(row.get("close", float("nan")))
    ema200 = float(row.get("ema_200", float("nan")))
    adx = float(row.get("adx_14", float("nan")))
    vol = float(row.get("volume", float("nan")))
    vol_sma = float(row.get("volume_sma_20", float("nan")))

    if math.isnan(price) or math.isnan(ema200) or math.isnan(adx):
        # Not enough history for EMA200/ADX yet.
        return default

    above_ema200 = price > ema200
    volume_confirmed = (not math.isnan(vol) and not math.isnan(vol_sma)
                        and vol_sma > 0 and vol > vol_mult * vol_sma)

    # --- Trend classification (per spec) -------------------------------
    if adx < adx_weak:
        trend, strength = "SIDEWAYS", "WEAK"
    else:
        trend = "UPTREND" if above_ema200 else "DOWNTREND"
        strength = "STRONG" if adx > adx_strong else "MODERATE"

    # --- Market score (0-100) ------------------------------------------
    score = 0
    if strength == "STRONG":
        score += 40
    elif strength == "MODERATE":
        score += 25
    if volume_confirmed:
        score += 20
    if above_ema200:
        score += 20
    if adx > adx_strong:
        score += 20
    score = min(score, 100)

    return {
        "trend": trend,
        "trend_strength": strength,
        "volume_confirmed": volume_confirmed,
        "above_ema200": above_ema200,
        "adx_value": round(adx, 2),
        "market_score": score,
    }
