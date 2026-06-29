"""
Fixed-Range Volume Profile (FRVP) — Phase 6 add-on.

Mechanical definition
---------------------
Over a price window, bin the price range into N levels and sum traded volume per
level (each candle's volume is spread across the levels its high-low spans).

  POC (Point of Control) = the highest-volume level.
  Value Area = the contiguous band around the POC holding 70% of total volume;
               VAH/VAL are its upper/lower edges.
  HVN / LVN  = high- / low-volume nodes (sticky vs. fast-move levels).

These are exact, computable levels — the basis of the mentor's FRVP method.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from utils.helpers import load_config

_VALUE_AREA_PCT = 0.70


def compute_frvp(df: pd.DataFrame, bins: Optional[int] = None
                 ) -> Optional[dict[str, Any]]:
    """Compute the volume profile for `df`. Returns POC/VAH/VAL/HVN/LVN or None."""
    if df is None or len(df) < 5:
        return None
    if bins is None:
        bins = int(load_config().get("frvp_bins", 24))
    bins = max(8, bins)

    low = df["low"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    lo, hi = float(low.min()), float(high.max())
    if hi <= lo:
        return None

    edges = np.linspace(lo, hi, bins + 1)
    span = hi - lo
    profile = np.zeros(bins)

    # Spread each candle's volume across the bins its range covers.
    for lc, hc, v in zip(low, high, vol):
        if v <= 0:
            continue
        b_lo = min(bins - 1, max(0, int((lc - lo) / span * bins)))
        b_hi = min(bins - 1, max(0, int((hc - lo) / span * bins)))
        n = b_hi - b_lo + 1
        profile[b_lo:b_hi + 1] += v / n

    total = profile.sum()
    if total <= 0:
        return None

    poc_idx = int(profile.argmax())
    # Value area: expand outward from the POC, always taking the heavier side,
    # until 70% of total volume is enclosed.
    cum = profile[poc_idx]
    lo_idx = hi_idx = poc_idx
    target = _VALUE_AREA_PCT * total
    while cum < target and (lo_idx > 0 or hi_idx < bins - 1):
        above = profile[hi_idx + 1] if hi_idx + 1 < bins else -1.0
        below = profile[lo_idx - 1] if lo_idx - 1 >= 0 else -1.0
        if above >= below:
            hi_idx += 1
            cum += profile[hi_idx]
        else:
            lo_idx -= 1
            cum += profile[lo_idx]

    def center(i: int) -> float:
        return float((edges[i] + edges[i + 1]) / 2)

    mean, std = profile.mean(), profile.std()
    hvn = [center(i) for i in range(bins) if profile[i] >= mean + std]
    lvn = [center(i) for i in range(bins) if profile[i] <= max(0.0, mean - std)]

    return {
        "poc": center(poc_idx),
        "vah": float(edges[hi_idx + 1]),
        "val": float(edges[lo_idx]),
        "hvn": hvn,
        "lvn": lvn,
        "total_volume": float(total),
        "bins": bins,
    }
