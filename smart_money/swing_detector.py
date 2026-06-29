"""
Swing detector (Phase 6).

Mechanical definitions
----------------------
SWING HIGH: a candle whose high is strictly greater than the highs of N candles
            on each side (N = config `swing_lookback`, default 2).
SWING LOW : a candle whose low is strictly less than the lows of N each side.

Structure: walking swings in time order, each swing high is HH/LH vs the prior
swing high, each swing low is HL/LL vs the prior swing low. This is the backbone
for deviation, BOS, and MSS.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from utils.helpers import load_config


def find_swings(df: pd.DataFrame, lookback: Optional[int] = None) -> pd.DataFrame:
    """Add swing columns to `df` (does not mutate the input).

    Columns added: swing_high (bool), swing_low (bool),
    swing_high_price (float/NaN), swing_low_price (float/NaN).
    """
    if lookback is None:
        lookback = int(load_config().get("swing_lookback", 2))
    out = df.copy().reset_index(drop=True)
    n = len(out)
    sh = np.zeros(n, dtype=bool)
    sl = np.zeros(n, dtype=bool)
    if n < 2 * lookback + 1:
        out["swing_high"] = sh
        out["swing_low"] = sl
        out["swing_high_price"] = np.nan
        out["swing_low_price"] = np.nan
        return out

    highs = out["high"].to_numpy(dtype=float)
    lows = out["low"].to_numpy(dtype=float)
    for i in range(lookback, n - lookback):
        hi = highs[i]
        if all(hi > highs[i - k] for k in range(1, lookback + 1)) and \
           all(hi > highs[i + k] for k in range(1, lookback + 1)):
            sh[i] = True
        lo = lows[i]
        if all(lo < lows[i - k] for k in range(1, lookback + 1)) and \
           all(lo < lows[i + k] for k in range(1, lookback + 1)):
            sl[i] = True

    out["swing_high"] = sh
    out["swing_low"] = sl
    out["swing_high_price"] = np.where(sh, highs, np.nan)
    out["swing_low_price"] = np.where(sl, lows, np.nan)
    return out


def get_structure(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Return the ordered HH/LH/HL/LL structure-point sequence.

    Each point: {index, price, kind ('high'/'low'), type ('HH'/'LH'/'HL'/'LL')}.
    Returns [] when there aren't enough candles/swings.
    """
    if "swing_high" not in df.columns:
        df = find_swings(df)
    df = df.reset_index(drop=True)

    points: list[dict[str, Any]] = []
    last_high: Optional[float] = None
    last_low: Optional[float] = None

    for i in range(len(df)):
        if bool(df["swing_high"].iat[i]):
            price = float(df["high"].iat[i])
            ptype = "HH" if (last_high is not None and price > last_high) else \
                    ("LH" if last_high is not None else "HH")
            points.append({"index": i, "price": price, "kind": "high",
                           "type": ptype})
            last_high = price
        if bool(df["swing_low"].iat[i]):
            price = float(df["low"].iat[i])
            ptype = "HL" if (last_low is not None and price > last_low) else \
                    ("LL" if last_low is not None else "HL")
            points.append({"index": i, "price": price, "kind": "low",
                           "type": ptype})
            last_low = price

    points.sort(key=lambda p: p["index"])
    return points


def last_swing_prices(df: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    """Return (last_swing_high_price, last_swing_low_price) or (None, None)."""
    if "swing_high" not in df.columns:
        df = find_swings(df)
    highs = df.loc[df["swing_high"], "high"]
    lows = df.loc[df["swing_low"], "low"]
    sh = float(highs.iloc[-1]) if len(highs) else None
    sl = float(lows.iloc[-1]) if len(lows) else None
    return sh, sl
