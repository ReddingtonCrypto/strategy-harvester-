"""
Liquidity sweep detector (Phase 6).

Mechanical definition
---------------------
A SWEEP of a level = a candle whose WICK crosses beyond a reference level (a
prior swing high/low or range edge) by at least `sweep_min_wick_pct`, but whose
BODY (close) finishes back on the original side.

  direction "high": high > level*(1 + pct) AND close < level   (swept highs)
  direction "low" : low  < level*(1 - pct) AND close > level    (swept lows)
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from utils.helpers import load_config


def _wick_frac() -> float:
    """Min wick overshoot beyond the level, as a fraction (config %, default .05%)."""
    return float(load_config().get("sweep_min_wick_pct", 0.05)) / 100.0


def detect_sweep(df: pd.DataFrame, level: float, direction: str,
                 start: int = 0) -> dict[str, Any]:
    """Detect the most recent sweep of `level` in `direction` from `start`.

    Returns {swept, index, level, direction}. `index` is the candle where the
    sweep occurred (most recent), or None.
    """
    result = {"swept": False, "index": None, "level": level,
              "direction": direction}
    if df is None or len(df) == 0 or level is None:
        return result

    frac = _wick_frac()
    df = df.reset_index(drop=True)
    found = None
    for i in range(max(0, start), len(df)):
        high = float(df["high"].iat[i])
        low = float(df["low"].iat[i])
        close = float(df["close"].iat[i])
        if direction == "high":
            if high > level * (1 + frac) and close < level:
                found = i
        elif direction == "low":
            if low < level * (1 - frac) and close > level:
                found = i
    if found is not None:
        result["swept"] = True
        result["index"] = found
    return result


def last_sweep(df: pd.DataFrame, swing_high: Optional[float],
               swing_low: Optional[float]) -> Optional[dict[str, Any]]:
    """Return the most recent sweep of either the given swing high or low."""
    candidates = []
    if swing_high is not None:
        s = detect_sweep(df, swing_high, "high")
        if s["swept"]:
            candidates.append(s)
    if swing_low is not None:
        s = detect_sweep(df, swing_low, "low")
        if s["swept"]:
            candidates.append(s)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["index"])
