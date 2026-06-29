"""
Fair Value Gap (FVG) detection (Phase 6).

Mechanical definition
---------------------
Bullish FVG: a 3-candle pattern where candle1.high < candle3.low (a gap up that
   leaves an imbalance below). Zone = [candle1.high, candle3.low].
Bearish FVG: candle1.low > candle3.high. Zone = [candle3.high, candle1.low].

A gap counts only if its size ≥ `fvg_min_gap_pct` of price (noise filter).
An FVG is "filled" once later price trades back through the gap.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from utils.helpers import load_config


def find_fvgs(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Return all FVG zones: {type, top, bottom, index, filled}.

    `index` is the 3rd candle of the pattern. `top`/`bottom` bound the gap.
    """
    if df is None or len(df) < 3:
        return []
    min_gap = float(load_config().get("fvg_min_gap_pct", 0.03)) / 100.0
    df = df.reset_index(drop=True)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(df)
    fvgs: list[dict[str, Any]] = []

    for i in range(2, n):
        c1h, c1l = highs[i - 2], lows[i - 2]
        c3h, c3l = highs[i], lows[i]
        # Bullish gap: candle1.high < candle3.low
        if c1h < c3l:
            gap = c3l - c1h
            if c1h > 0 and gap / c1h >= min_gap:
                fvgs.append({"type": "bullish", "top": c3l, "bottom": c1h,
                             "index": i, "filled": False})
        # Bearish gap: candle1.low > candle3.high
        elif c1l > c3h:
            gap = c1l - c3h
            if c3h > 0 and gap / c3h >= min_gap:
                fvgs.append({"type": "bearish", "top": c1l, "bottom": c3h,
                             "index": i, "filled": False})

    # Mark filled FVGs (price later traded back into the gap).
    for f in fvgs:
        after_low = lows[f["index"] + 1:]
        after_high = highs[f["index"] + 1:]
        if f["type"] == "bullish" and len(after_low):
            f["filled"] = bool((after_low <= f["bottom"]).any())
        elif f["type"] == "bearish" and len(after_high):
            f["filled"] = bool((after_high >= f["top"]).any())
    return fvgs


def is_fvg_unfilled(df: pd.DataFrame, fvg: dict[str, Any]) -> bool:
    """True if price has not traded back through the gap since it formed."""
    if not fvg:
        return False
    df = df.reset_index(drop=True)
    after = df.iloc[fvg["index"] + 1:]
    if after.empty:
        return True
    if fvg["type"] == "bullish":
        return bool((after["low"] > fvg["bottom"]).all())
    return bool((after["high"] < fvg["top"]).all())
