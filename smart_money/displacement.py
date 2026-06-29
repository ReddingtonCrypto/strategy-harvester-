"""
Displacement / expansion detection (Phase 6).

Mechanical definition
---------------------
A DISPLACEMENT candle has a body greater than `displacement_body_mult` (default
2.0) times the average body of the last `displacement_lookback` candles (default
20), AND it creates a fair value gap (an imbalance with the candle two back).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from utils.helpers import load_config


def detect_displacement(df: pd.DataFrame) -> dict[str, Any]:
    """Return {found, indices, directions} for displacement candles."""
    result = {"found": False, "indices": [], "directions": []}
    if df is None or len(df) < 5:
        return result
    config = load_config()
    mult = float(config.get("displacement_body_mult", 2.0))
    lb = int(config.get("displacement_lookback", 20))

    df = df.reset_index(drop=True)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    body = (c - o)
    abody = abs(body)
    n = len(df)

    for i in range(max(2, lb), n):
        avg = abody[i - lb:i].mean()
        if avg <= 0:
            continue
        if abody[i] <= mult * avg:
            continue
        # must also create an FVG with candle i-2
        bull_gap = h[i - 2] < low[i]
        bear_gap = low[i - 2] > h[i]
        if bull_gap or bear_gap:
            result["indices"].append(i)
            result["directions"].append("UP" if body[i] > 0 else "DOWN")

    result["found"] = len(result["indices"]) > 0
    return result
