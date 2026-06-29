"""
Smart-Money orchestrator (Phase 6).

Ties the detectors together into a unified market state and the three
backtestable signal generators (Range, CRT, Textbook) plus the Deviation
filter. Pure Python, zero LLM. Each signal generator returns a list of LONG
entry dicts (the system trades spot, so entries are long-only):

    {index, direction, entry, target, stop, type, ...flags}

`get_entries`/`latest_entry` are the clean hooks the backtest runner and live
scanner call.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from smart_money import (displacement, fvg, market_structure, order_blocks,
                         ranges, swing_detector, volume_profile)
from smart_money.fib_zones import in_discount_zone
from utils.helpers import load_config


# --- Unified state -------------------------------------------------------

def analyze(df: pd.DataFrame) -> dict[str, Any]:
    """Run all detectors and return a unified market-state dict.

    Never raises on insufficient data — returns sensible empties instead.
    """
    if df is None or len(df) < 10:
        return {"structure": [], "trend": "RANGE", "range": {}, "recent_sweep": None,
                "mss": {}, "fvgs": [], "order_blocks": [], "displacement": {},
                "last_swing_high": None, "last_swing_low": None}

    swung = swing_detector.find_swings(df)
    structure = swing_detector.get_structure(swung)
    last_sh, last_sl = swing_detector.last_swing_prices(swung)
    from smart_money.liquidity_sweep import last_sweep
    return {
        "structure": structure,
        "trend": market_structure.trend_from_structure(swung),
        "range": ranges.detect_range(df),
        "recent_sweep": last_sweep(df, last_sh, last_sl),
        "mss": market_structure.detect_mss(df),
        "fvgs": fvg.find_fvgs(df),
        "order_blocks": order_blocks.find_order_blocks(df),
        "displacement": displacement.detect_displacement(df),
        "last_swing_high": last_sh,
        "last_swing_low": last_sl,
    }


def _frac() -> float:
    return float(load_config().get("sweep_min_wick_pct", 0.05)) / 100.0


def _atr_series(df: pd.DataFrame, period: int = 14):
    """Wilder ATR as a numpy array aligned to df rows (for stop buffering)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift()
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean().to_numpy(dtype=float)


def _buffered_stop(raw_stop: float, atr_val: float) -> float:
    """Place the stop below the swept wick by `smc_stop_buffer_atr` × ATR."""
    import math
    buf = float(load_config().get("smc_stop_buffer_atr", 1.0))
    if atr_val is None or (isinstance(atr_val, float) and math.isnan(atr_val)):
        return raw_stop
    return raw_stop - buf * atr_val


def _rr_ok(entry: float, stop: float, target: float) -> bool:
    """True if it's a valid long with reward/risk ≥ `smc_min_rr`."""
    min_rr = float(load_config().get("smc_min_rr", 1.5))
    if not (stop < entry < target):
        return False
    risk = entry - stop
    reward = target - entry
    return risk > 0 and (reward / risk) >= min_rr


# --- Strategy signal generators (full-series, for backtesting) -----------

def signal_range_strategy(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Range card: sweep range_low + close inside → LONG, target range_high.

    Filters (mentor's rules): requires the mid-range RECLAIM confirmation
    (`range_require_mid_reclaim`); stop = swept low − ATR buffer; min R:R gate.
    """
    if df is None or len(df) < 30:
        return []
    config = load_config()
    lb = int(config.get("range_lookback", 40))
    require_reclaim = bool(config.get("range_require_mid_reclaim", True))
    frac = _frac()
    df = df.reset_index(drop=True)
    atr = _atr_series(df)
    entries: list[dict[str, Any]] = []
    for i in range(lb, len(df)):
        r = ranges.detect_range(df.iloc[i - lb:i])
        if not r["is_range"]:
            continue
        cur = df.iloc[i]
        low, close = float(cur["low"]), float(cur["close"])
        rl, rh, rm = r["range_low"], r["range_high"], r["range_mid"]
        if not (low < rl * (1 - frac) and rl < close < rh):
            continue
        confirmation = close > rm        # reclaimed the mid after the low sweep
        if require_reclaim and not confirmation:
            continue
        stop = _buffered_stop(low, atr[i])
        if not _rr_ok(close, stop, rh):
            continue
        entries.append({"index": i, "direction": "LONG", "entry": close,
                        "target": rh, "stop": stop, "type": "range",
                        "confirmation": confirmation})
    return entries


def signal_crt(df: pd.DataFrame) -> list[dict[str, Any]]:
    """CRT card: C2 sweeps C1 low + closes inside → LONG, target C1 high.

    Filters (mentor's rules): requires ≥ `crt_required_confluence` of
    {OB, FVG, old-level} alignment; stop = swept low − ATR buffer; min R:R gate.
    """
    if df is None or len(df) < 10:
        return []
    config = load_config()
    need_conf = int(config.get("crt_required_confluence", 2))
    frac = _frac()
    df = df.reset_index(drop=True)
    atr = _atr_series(df)
    fvgs = [f for f in fvg.find_fvgs(df) if f["type"] == "bullish" and not f["filled"]]
    obs = [b for b in order_blocks.find_order_blocks(df) if b["type"] == "bullish"]
    # "old levels" = prior swing highs/lows (key liquidity levels)
    swung = swing_detector.find_swings(df)
    old_levels = ([float(x) for x in swung.loc[swung["swing_high"], "high"]]
                  + [float(x) for x in swung.loc[swung["swing_low"], "low"]])

    entries: list[dict[str, Any]] = []
    for i in range(1, len(df)):
        c1h, c1l = float(df["high"].iat[i - 1]), float(df["low"].iat[i - 1])
        low, close = float(df["low"].iat[i]), float(df["close"].iat[i])
        if not (low < c1l * (1 - frac) and c1l < close < c1h):
            continue
        tol = 0.5 * atr[i] if atr[i] == atr[i] else close * 0.002  # NaN-safe
        aligned_fvg = any(f["bottom"] <= close <= f["top"] for f in fvgs)
        aligned_ob = any(b["bottom"] <= close <= b["top"] for b in obs)
        aligned_old = any(abs(close - lvl) <= tol for lvl in old_levels)
        confluence = int(aligned_fvg) + int(aligned_ob) + int(aligned_old)
        if confluence < need_conf:
            continue
        stop = _buffered_stop(low, atr[i])
        if not _rr_ok(close, stop, c1h):
            continue
        entries.append({"index": i, "direction": "LONG", "entry": close,
                        "target": c1h, "stop": stop, "type": "crt",
                        "aligned_with_fvg": aligned_fvg, "aligned_with_ob": aligned_ob,
                        "aligned_with_old_level": aligned_old,
                        "confluence_count": confluence})
    return entries


def signal_frvp(df: pd.DataFrame) -> list[dict[str, Any]]:
    """FRVP card: in a range, a reclaim of VAL after sweeping below it → LONG,
    target POC (the volume magnet).

    Filters: only fires when the window is a range (mentor's rule); stop = swept
    low − ATR buffer; min R:R gate. Entry requires room below the POC.
    """
    if df is None or len(df) < 30:
        return []
    lb = int(load_config().get("range_lookback", 40))
    frac = _frac()
    df = df.reset_index(drop=True)
    atr = _atr_series(df)
    entries: list[dict[str, Any]] = []
    for i in range(lb, len(df)):
        window = df.iloc[i - lb:i]
        if not ranges.detect_range(window)["is_range"]:  # FRVP only in ranges
            continue
        prof = volume_profile.compute_frvp(window)
        if not prof:
            continue
        val, poc = prof["val"], prof["poc"]
        cur = df.iloc[i]
        low, close = float(cur["low"]), float(cur["close"])
        # LONG: swept below VAL, closed back above it, with room up to the POC.
        if low < val * (1 - frac) and val < close < poc:
            stop = _buffered_stop(low, atr[i])
            if not _rr_ok(close, stop, poc):
                continue
            entries.append({"index": i, "direction": "LONG", "entry": close,
                            "target": poc, "stop": stop, "type": "frvp",
                            "val": val, "poc": poc, "vah": prof["vah"]})
    return entries


def signal_textbook(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Textbook A+: downtrend → MSS-up → retrace into discount zone → LONG.

    Target = the high that the MSS broke through; stop = below the swept low.
    Strictest generator (fewest, highest-quality entries).
    """
    if df is None or len(df) < 30:
        return []
    df = swing_detector.find_swings(df).reset_index(drop=True)
    h = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    sh = df["swing_high"].to_numpy(dtype=bool)
    sl = df["swing_low"].to_numpy(dtype=bool)
    atr = _atr_series(df)
    n = len(df)

    entries: list[dict[str, Any]] = []
    prev_sh = None        # previous swing-high price (to confirm a lower-high)
    last_sh = None        # most recent swing-high price
    last_sh_i = None
    last_sl = None        # most recent swing-low price (leg origin)
    pending = None        # an armed MSS setup awaiting retracement

    for i in range(n):
        if sh[i]:
            prev_sh, last_sh, last_sh_i = last_sh, h[i], i
        if sl[i]:
            last_sl = low[i]

        # MSS-up: close breaks a LOWER high (last_sh < prev_sh) → change of character
        if (pending is None and last_sh is not None and prev_sh is not None
                and last_sh < prev_sh and last_sl is not None
                and c[i] > last_sh):
            leg = (last_sl, h[i])          # origin low → break high
            pending = {"target": float(last_sh), "stop": float(last_sl),
                       "leg": leg, "armed_at": i, "expires": i + 20}

        # Wait for a retrace into the discount zone, then enter.
        elif pending is not None:
            if i > pending["expires"]:
                pending = None
            elif in_discount_zone(low[i], pending["leg"]) and c[i] > pending["stop"]:
                entry = float(c[i])
                stop = _buffered_stop(pending["stop"], atr[i])  # below swept low
                target = pending["target"]
                if _rr_ok(entry, stop, target):
                    entries.append({"index": i, "direction": "LONG",
                                    "entry": entry, "target": target,
                                    "stop": stop, "type": "textbook"})
                pending = None
    return entries


def deviation_probability(df: pd.DataFrame) -> str:
    """'HIGH' if the latest up-move broke a prior higher-high, else 'LOW'.

    Used as a FILTER: downgrade/skip longs when LOW (stair-step liquidity build).
    """
    if df is None or len(df) < 10:
        return "LOW"
    structure = swing_detector.get_structure(swing_detector.find_swings(df))
    highs = [p for p in structure if p["kind"] == "high"]
    if len(highs) >= 2 and highs[-1]["price"] > highs[-2]["price"]:
        return "HIGH"
    return "LOW"


# --- Routing hooks -------------------------------------------------------

_SIGNALS = {
    "range": signal_range_strategy,
    "crt": signal_crt,
    "textbook": signal_textbook,
    "frvp": signal_frvp,
}


def get_entries(df: pd.DataFrame, engine_signal: str) -> list[dict[str, Any]]:
    """Return all entry signals for the given engine_signal ('filter' → [])."""
    fn = _SIGNALS.get(engine_signal)
    return fn(df) if fn else []


def latest_entry(df: pd.DataFrame, engine_signal: str) -> Optional[dict[str, Any]]:
    """Return an entry on the latest CLOSED candle (index n-2), else None.

    Live data's final row is the forming candle, so 'latest closed' is n-2.
    """
    if df is None or len(df) < 3:
        return None
    closed_idx = len(df) - 2
    for e in get_entries(df, engine_signal):
        if e["index"] == closed_idx:
            return e
    return None
