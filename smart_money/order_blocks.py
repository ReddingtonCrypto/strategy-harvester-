"""
Order block detection (Phase 6).

Mechanical definition
---------------------
Bullish OB: the last bearish (down) candle before an up-move that breaks
   structure. Its high-low defines the OB zone.
Bearish OB: the last bullish (up) candle before a down-move that breaks
   structure.

Mechanical version used here: walk forward tracking the most recent confirmed
swing high/low; when a candle CLOSES through it (a break), the last opposite-
colour candle within `ob_lookback` before the break is the order block.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from smart_money.swing_detector import find_swings
from utils.helpers import load_config


def find_order_blocks(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Return order-block zones: {type, top, bottom, index, mitigated}."""
    if df is None or len(df) < 10:
        return []
    lb = int(load_config().get("ob_lookback", 30))
    df = find_swings(df).reset_index(drop=True)
    n = len(df)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    sh = df["swing_high"].to_numpy(dtype=bool)
    sl = df["swing_low"].to_numpy(dtype=bool)

    obs: list[dict[str, Any]] = []
    last_sh = last_sl = None
    seen_idx = set()

    for i in range(n):
        # update confirmed swing levels as we pass them
        if sh[i]:
            last_sh = h[i]
        if sl[i]:
            last_sl = low[i]

        # bullish OB: close breaks above the last swing high
        if last_sh is not None and c[i] > last_sh:
            for j in range(i - 1, max(-1, i - lb - 1), -1):
                if c[j] < o[j]:                       # last bearish candle
                    if j not in seen_idx:
                        obs.append({"type": "bullish", "top": h[j],
                                    "bottom": low[j], "index": j,
                                    "mitigated": False})
                        seen_idx.add(j)
                    break
            last_sh = None                            # consume the break

        # bearish OB: close breaks below the last swing low
        if last_sl is not None and c[i] < last_sl:
            for j in range(i - 1, max(-1, i - lb - 1), -1):
                if c[j] > o[j]:                       # last bullish candle
                    if j not in seen_idx:
                        obs.append({"type": "bearish", "top": h[j],
                                    "bottom": low[j], "index": j,
                                    "mitigated": False})
                        seen_idx.add(j)
                    break
            last_sl = None

    # mitigated = price later traded back into the OB zone
    for ob in obs:
        after = df.iloc[ob["index"] + 1:]
        if not after.empty:
            ob["mitigated"] = bool(
                ((after["low"] <= ob["top"]) & (after["high"] >= ob["bottom"])).any())
    return obs
