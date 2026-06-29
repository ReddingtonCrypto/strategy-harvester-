"""
Market structure: BOS + MSS/CHOCH (Phase 6).

Mechanical definitions
----------------------
BOS (Break of Structure): a close beyond the most recent confirmed swing high
   (bullish BOS) or swing low (bearish BOS), IN the direction of the trend.
MSS / CHOCH (Change of Character): the FIRST break of structure AGAINST the
   prevailing trend. In a downtrend (LH/LL), an MSS-up = a close above the most
   recent lower-high. In an uptrend, MSS-down = close below the most recent
   higher-low.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from smart_money.swing_detector import find_swings, get_structure


def trend_from_structure(df: pd.DataFrame) -> str:
    """Return 'UP' / 'DOWN' / 'RANGE' from the last two highs and lows."""
    pts = get_structure(df)
    highs = [p for p in pts if p["kind"] == "high"]
    lows = [p for p in pts if p["kind"] == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        up = highs[-1]["price"] > highs[-2]["price"] and \
            lows[-1]["price"] > lows[-2]["price"]
        down = highs[-1]["price"] < highs[-2]["price"] and \
            lows[-1]["price"] < lows[-2]["price"]
        if up:
            return "UP"
        if down:
            return "DOWN"
    return "RANGE"


def _last_swing(df: pd.DataFrame, kind: str):
    """Return (index, price) of the last swing of `kind` ('high'/'low'), or None."""
    col = "swing_high" if kind == "high" else "swing_low"
    price_col = "high" if kind == "high" else "low"
    idxs = df.index[df[col]].tolist()
    if not idxs:
        return None
    i = idxs[-1]
    return i, float(df[price_col].iat[i])


def detect_mss(df: pd.DataFrame) -> dict[str, Any]:
    """Detect a Change-of-Character (first counter-trend structure break).

    Returns {mss_found, direction, index, broken_level}.
    """
    result = {"mss_found": False, "direction": None, "index": None,
              "broken_level": None}
    if df is None or len(df) < 10:
        return result
    df = find_swings(df).reset_index(drop=True)
    trend = trend_from_structure(df)

    if trend == "DOWN":
        sw = _last_swing(df, "high")          # most recent LH
        if sw is None:
            return result
        idx, level = sw
        for i in range(idx + 1, len(df)):     # first close above the LH
            if float(df["close"].iat[i]) > level:
                return {"mss_found": True, "direction": "UP", "index": i,
                        "broken_level": level}
    elif trend == "UP":
        sw = _last_swing(df, "low")           # most recent HL
        if sw is None:
            return result
        idx, level = sw
        for i in range(idx + 1, len(df)):     # first close below the HL
            if float(df["close"].iat[i]) < level:
                return {"mss_found": True, "direction": "DOWN", "index": i,
                        "broken_level": level}
    return result


def detect_bos(df: pd.DataFrame) -> dict[str, Any]:
    """Detect the most recent with-trend break of structure.

    Returns {bos_found, direction, index, broken_level}.
    """
    result = {"bos_found": False, "direction": None, "index": None,
              "broken_level": None}
    if df is None or len(df) < 10:
        return result
    df = find_swings(df).reset_index(drop=True)
    trend = trend_from_structure(df)

    if trend == "UP":
        sw = _last_swing(df, "high")
        if sw is None:
            return result
        idx, level = sw
        for i in range(len(df) - 1, idx, -1):
            if float(df["close"].iat[i]) > level:
                return {"bos_found": True, "direction": "UP", "index": i,
                        "broken_level": level}
    elif trend == "DOWN":
        sw = _last_swing(df, "low")
        if sw is None:
            return result
        idx, level = sw
        for i in range(len(df) - 1, idx, -1):
            if float(df["close"].iat[i]) < level:
                return {"bos_found": True, "direction": "DOWN", "index": i,
                        "broken_level": level}
    return result
