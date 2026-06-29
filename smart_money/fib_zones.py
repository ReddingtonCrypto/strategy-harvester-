"""
Fibonacci retracement zones (Phase 6).

Mechanical definition
---------------------
Given a swing leg (swing_start_price → swing_end_price), retracement level price
= end - (end - start) * ratio. The DISCOUNT zone (for longs) is the
`discount_zone` band (default 0.705–0.786) — a deep retracement back toward the
leg's origin. PREMIUM is the mirror band for shorts.
"""

from __future__ import annotations

from typing import Any

from utils.helpers import load_config

_RATIOS = [0.5, 0.618, 0.66, 0.705, 0.71, 0.75, 0.786]


def fib_from_leg(swing_start_price: float, swing_end_price: float
                 ) -> dict[str, float]:
    """Return {ratio: price} for the standard retracement ratios of a leg."""
    start, end = float(swing_start_price), float(swing_end_price)
    return {r: end - (end - start) * r for r in _RATIOS}


def in_discount_zone(price: float, leg: tuple[float, float]) -> bool:
    """True if `price` is within the 0.705–0.786 retracement band of `leg`.

    `leg` = (swing_start_price, swing_end_price).
    """
    band = load_config().get("discount_zone", [0.705, 0.786])
    lo_r, hi_r = float(band[0]), float(band[1])
    start, end = float(leg[0]), float(leg[1])
    p1 = end - (end - start) * lo_r
    p2 = end - (end - start) * hi_r
    low, high = min(p1, p2), max(p1, p2)
    return low <= float(price) <= high
