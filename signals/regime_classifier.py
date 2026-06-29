"""
Market regime classifier (Phase 5.5, Part 4).

Pure-Python (no LLM) classification of the current market into one of five
regimes, used to gate signals so strategies only fire when conditions suit
them. Reuses the Phase 2 indicator engine; reads the latest CLOSED candle.

Regimes: BULL_TRENDING, BEAR_TRENDING, BULL_VOLATILE, BEAR_VOLATILE, SIDEWAYS.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from backtesting import indicator_engine
from utils.helpers import load_config

# Volatility (ATR/price) thresholds.
_VOL_HIGH = 0.03    # > 3% → volatile
_VOL_LOW = 0.015    # < 1.5% → calm
# Momentum (14-period ROC) thresholds, in percent.
_ROC_STRONG = 5.0


def _closed_idx(df: pd.DataFrame) -> int:
    """Index of the latest CLOSED candle (last row is the forming one)."""
    return len(df) - 2 if len(df) >= 2 else len(df) - 1


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift()
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def classify_regime(df: pd.DataFrame, asset: str = "") -> dict[str, Any]:
    """Classify the current market regime from OHLCV data."""
    default = {
        "regime": "SIDEWAYS", "confidence": 0, "trend_direction": "NEUTRAL",
        "trend_strength": "WEAK", "volatility": "CALM", "momentum": "NEUTRAL",
        "volume_trend": "NEUTRAL", "suitable_for_signals": False,
        "reason": "Insufficient data to classify.",
    }
    if df is None or len(df) < 30:
        return default

    if "ema_200" not in df.columns or "adx_14" not in df.columns:
        df = indicator_engine.calculate_all_indicators(df)
    df = df.copy()
    df["atr_14"] = _atr(df)
    df["roc_14"] = (df["close"] - df["close"].shift(14)) / \
        df["close"].shift(14) * 100

    i = _closed_idx(df)
    row = df.iloc[i]
    price = float(row["close"])
    ema200 = float(row.get("ema_200", float("nan")))
    adx = float(row.get("adx_14", float("nan")))
    atr = float(row.get("atr_14", float("nan")))
    roc = float(row.get("roc_14", float("nan")))

    if any(math.isnan(v) for v in (ema200, adx, atr)):
        return default

    # 1) Trend direction
    direction = "BULL" if price > ema200 else "BEAR"
    # 2) Trend strength
    if adx > 25:
        strength = "STRONG"
    elif adx < 20:
        strength = "WEAK"
    else:
        strength = "MODERATE"
    # 3) Volatility
    vol_ratio = atr / price if price else 0
    if vol_ratio > _VOL_HIGH:
        volatility = "VOLATILE"
    elif vol_ratio < _VOL_LOW:
        volatility = "CALM"
    else:
        volatility = "MODERATE"
    # 4) Momentum
    if not math.isnan(roc) and roc > _ROC_STRONG:
        momentum = "POSITIVE"
    elif not math.isnan(roc) and roc < -_ROC_STRONG:
        momentum = "NEGATIVE"
    else:
        momentum = "NEUTRAL"
    # 5) Volume trend (volume SMA now vs ~5 candles earlier)
    volume_trend = "NEUTRAL"
    if "volume_sma_20" in df.columns and i - 5 >= 0:
        now_v = df["volume_sma_20"].iloc[i]
        prev_v = df["volume_sma_20"].iloc[i - 5]
        if not (math.isnan(now_v) or math.isnan(prev_v)):
            volume_trend = "ACCUMULATION" if now_v > prev_v else "DISTRIBUTION"

    # --- Regime matrix --------------------------------------------------
    ranging = adx < 20
    volatile = volatility == "VOLATILE"
    if ranging:
        regime = "SIDEWAYS"
    elif direction == "BULL":
        regime = "BULL_VOLATILE" if volatile else "BULL_TRENDING"
    else:
        regime = "BEAR_VOLATILE" if volatile else "BEAR_TRENDING"

    suitable = regime in ("BULL_TRENDING", "BEAR_TRENDING",
                          "BULL_VOLATILE", "BEAR_VOLATILE")
    if regime == "SIDEWAYS":
        suitable = False

    confidence = _regime_confidence(price, ema200, adx, roc)
    reason = _regime_reason(direction, strength, volatility, momentum)

    return {
        "regime": regime, "confidence": confidence,
        "trend_direction": direction, "trend_strength": strength,
        "volatility": volatility, "momentum": momentum,
        "volume_trend": volume_trend, "suitable_for_signals": suitable,
        "reason": reason,
    }


def _regime_confidence(price: float, ema200: float, adx: float,
                       roc: float) -> int:
    """Heuristic 0-100 confidence in the regime call."""
    score = 40.0
    if ema200:
        score += min(20, abs(price - ema200) / ema200 * 100 * 2)
    score += min(25, max(0, adx - 10))  # stronger ADX → more confident
    if not math.isnan(roc):
        score += min(15, abs(roc))
    return int(max(0, min(100, round(score))))


def _regime_reason(direction: str, strength: str, volatility: str,
                   momentum: str) -> str:
    """Human-readable one-liner describing the regime."""
    d = "uptrend" if direction == "BULL" else "downtrend"
    return (f"{strength.title()} {d}, {volatility.lower()} volatility, "
            f"{momentum.lower()} momentum")


def is_regime_suitable(regime: dict[str, Any], strategy_card,
                       signal_type: str = "BUY") -> tuple[bool, str, int]:
    """Decide if the regime suits a strategy/direction.

    Returns (suitable, reason, confidence_delta). Confidence is reduced by 15
    in volatile regimes; SIDEWAYS is never suitable; BUY signals are skipped in
    BEAR_TRENDING and SELL signals in BULL_TRENDING.
    """
    name = regime.get("regime", "SIDEWAYS")
    if name == "SIDEWAYS":
        return False, "Sideways market — no clear direction.", 0

    if signal_type == "BUY" and name in ("BEAR_TRENDING", "BEAR_VOLATILE"):
        return False, f"BUY signal in {name} — against the trend.", 0
    if signal_type == "SELL" and name in ("BULL_TRENDING", "BULL_VOLATILE"):
        return False, f"SELL signal in {name} — against the trend.", 0

    delta = -15 if name in ("BULL_VOLATILE", "BEAR_VOLATILE") else 0
    return True, f"Regime {name} suits {signal_type}.", delta


def record_regime(asset: str, timeframe: str, regime: dict[str, Any]) -> None:
    """Persist a regime observation to regime_history (if enabled)."""
    if not load_config().get("regime_history_enabled", True):
        return
    from storage import database as db
    from utils.helpers import generate_id, utc_now_str
    db.save_regime({
        "id": generate_id(), "asset": asset, "timeframe": timeframe,
        "regime": regime.get("regime", "SIDEWAYS"),
        "confidence": int(regime.get("confidence", 0)),
        "recorded_at": utc_now_str(),
    })
