"""
Range detection (Phase 6).

Mechanical definition
---------------------
Over a `range_lookback` window (default 40 candles), if
(highest_high - lowest_low) / lowest_low is within `range_max_height_pct`
(default 12%) AND price has touched near both edges at least twice, treat it as
a range.

  range_high = highest high, range_low = lowest low,
  range_mid  = (range_high + range_low) / 2

range_signal implements the mentor's Range card: sweep an edge, close back
inside, target the opposite edge; reclaiming mid after a low sweep is added
confirmation.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from utils.helpers import load_config


def detect_range(df: pd.DataFrame) -> dict[str, Any]:
    """Detect whether the last `range_lookback` candles form a range."""
    config = load_config()
    lookback = int(config.get("range_lookback", 40))
    max_height = float(config.get("range_max_height_pct", 12))

    empty = {"is_range": False, "range_high": None, "range_low": None,
             "range_mid": None, "touches_high": 0, "touches_low": 0}
    if df is None or len(df) < max(10, lookback // 2):
        return empty

    window = df.iloc[-lookback:]
    rh = float(window["high"].max())
    rl = float(window["low"].min())
    if rl <= 0:
        return empty
    height_pct = (rh - rl) / rl * 100.0
    tol = (rh - rl) * 0.10  # "near" an edge = within 10% of range height
    touches_high = int((window["high"] >= rh - tol).sum())
    touches_low = int((window["low"] <= rl + tol).sum())

    is_range = (height_pct <= max_height and touches_high >= 2
                and touches_low >= 2)
    return {"is_range": is_range, "range_high": rh, "range_low": rl,
            "range_mid": (rh + rl) / 2.0, "touches_high": touches_high,
            "touches_low": touches_low}


def range_signal(df: pd.DataFrame) -> Optional[dict[str, Any]]:
    """Return a Range-card signal for the latest candle, or None.

    LONG  = sweep of range_low + close back inside → target range_high.
    SHORT = sweep of range_high + close back inside → target range_low.
    Reclaiming range_mid after a low sweep flags added confirmation.
    """
    r = detect_range(df)
    if not r["is_range"]:
        return None
    frac = float(load_config().get("sweep_min_wick_pct", 0.05)) / 100.0
    cur = df.iloc[-1]
    low, high, close = float(cur["low"]), float(cur["high"]), float(cur["close"])
    rl, rh, rm = r["range_low"], r["range_high"], r["range_mid"]

    # LONG: swept the low, closed back inside the range.
    if low < rl * (1 - frac) and rl < close < rh:
        return {"direction": "LONG", "entry": close, "target": rh, "stop": low,
                "range_mid": rm, "confirmation": close > rm, "type": "range"}
    # SHORT: swept the high, closed back inside.
    if high > rh * (1 + frac) and rl < close < rh:
        return {"direction": "SHORT", "entry": close, "target": rl, "stop": high,
                "range_mid": rm, "confirmation": close < rm, "type": "range"}
    return None
