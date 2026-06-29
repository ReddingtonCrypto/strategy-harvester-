"""
Auto parameter optimizer (Phase 5.5, Part 3).

Takes a strategy, detects the indicator parameters in its rule, grids over
candidate values, and runs a mini-backtest for each (reusing the Phase 2
backtest runner) to find the best-performing combination. Results are saved to
`parameter_optimizations` but NEVER auto-applied — they feed the daily
adaptation for user approval.
"""

from __future__ import annotations

import contextlib
import io
import re
from typing import Any, Optional

import pandas as pd

from backtesting import data_fetcher
from backtesting.backtest_runner import run_backtest
from models.strategy_card import StrategyCard
from storage import database as db
from storage import strategy_store
from utils.helpers import generate_id, today_str

# Candidate grids per parameter.
_RSI_THRESHOLDS = [20, 22, 25, 27, 28, 30, 32, 35]
_ADX_THRESHOLDS = [15, 20, 25, 30]
_MA_PERIODS = [20, 50, 200]   # columns the indicator engine actually computes


def _detect_variants(rule: str) -> tuple[dict[str, Any], list[tuple]]:
    """Return (original_params, variants) where each variant is

    (param_label, value, modified_rule).
    """
    original: dict[str, Any] = {}
    variants: list[tuple] = []

    # RSI threshold (number compared against rsi_14).
    m = re.search(r"rsi_14'\]\s*[<>]=?\s*(\d+(?:\.\d+)?)", rule)
    if m:
        orig = _num(m.group(1))
        original["rsi_threshold"] = orig
        for v in _RSI_THRESHOLDS:
            if v != orig:
                mod = rule[:m.start(1)] + str(v) + rule[m.end(1):]
                variants.append(("rsi_threshold", v, mod))

    # ADX threshold.
    m = re.search(r"adx_14'\]\s*[<>]=?\s*(\d+(?:\.\d+)?)", rule)
    if m:
        orig = _num(m.group(1))
        original["adx_threshold"] = orig
        for v in _ADX_THRESHOLDS:
            if v != orig:
                mod = rule[:m.start(1)] + str(v) + rule[m.end(1):]
                variants.append(("adx_threshold", v, mod))

    # SMA period swap.
    m = re.search(r"sma_(\d+)", rule)
    if m:
        orig = int(m.group(1))
        original["sma_period"] = orig
        for v in _MA_PERIODS:
            if v != orig:
                variants.append(("sma_period", v,
                                 rule.replace(f"sma_{orig}", f"sma_{v}")))

    # EMA period swap.
    m = re.search(r"ema_(\d+)", rule)
    if m:
        orig = int(m.group(1))
        original["ema_period"] = orig
        for v in _MA_PERIODS:
            if v != orig:
                variants.append(("ema_period", v,
                                 rule.replace(f"ema_{orig}", f"ema_{v}")))

    return original, variants


def _num(s: str):
    f = float(s)
    return int(f) if f == int(f) else f


def _win_rate(card: StrategyCard, entry_rule: str, asset: str,
              timeframe: str, df: pd.DataFrame) -> float:
    """Quietly backtest a rule variant and return its win rate."""
    temp = StrategyCard.from_dict({**card.to_dict(), "entry_rule": entry_rule})
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        report = run_backtest(temp, asset, timeframe, df=df.copy(), save=False)
    return report.win_rate


def optimize_parameters(strategy_id: str, asset: str = "BTC/USDT",
                        timeframe: str = "4h",
                        df: Optional[pd.DataFrame] = None) -> Optional[dict[str, Any]]:
    """Grid-search a strategy's parameters on one asset/timeframe."""
    card = strategy_store.get_card(strategy_id)
    if card is None or not card.entry_rule:
        print(f"⚠️  [Optimizer] {strategy_id} has no parsed entry rule.")
        return None

    if df is None:
        df = data_fetcher.fetch_ohlcv(asset, timeframe.lower())
    if df is None or len(df) < 30:
        print("⚠️  [Optimizer] Not enough data to optimize.")
        return None

    original_params, variants = _detect_variants(card.entry_rule)
    if not variants:
        print(f"ℹ️  [Optimizer] No tunable parameters found in "
              f"'{card.name}'.")
        return None

    base_wr = _win_rate(card, card.entry_rule, asset, timeframe, df)
    best_wr, best_label, best_value, best_rule = base_wr, None, None, card.entry_rule

    print(f"🔧 [Optimizer] {card.name} on {asset} {timeframe}: testing "
          f"{len(variants)} variants (baseline {base_wr}%)...")
    for label, value, mod_rule in variants:
        wr = _win_rate(card, mod_rule, asset, timeframe, df)
        if wr > best_wr:
            best_wr, best_label, best_value, best_rule = wr, label, value, mod_rule

    optimal_params = dict(original_params)
    if best_label is not None:
        optimal_params[best_label] = best_value

    result = {
        "strategy_id": strategy_id,
        "asset": asset,
        "timeframe": timeframe,
        "original_params": original_params,
        "optimal_params": optimal_params,
        "original_win_rate": round(base_wr, 1),
        "optimal_win_rate": round(best_wr, 1),
        "improvement": round(best_wr - base_wr, 1),
        "tested_combinations": len(variants),
        "best_combination_rank": 1,
        "best_rule": best_rule,
    }
    print(f"🔧 [Optimizer] best: {optimal_params} → {best_wr}% "
          f"(+{result['improvement']}%).")
    return result


def run_full_optimization(strategy_id: str) -> Optional[dict[str, Any]]:
    """Optimize across the strategy's assets/timeframe; save the best result.

    The result is stored with applied=False (never auto-applied).
    """
    card = strategy_store.get_card(strategy_id)
    if card is None:
        return None

    assets = [f"{a}/USDT" for a in (card.assets or ["BTC"])]
    timeframe = card.timeframe or "4h"

    best: Optional[dict[str, Any]] = None
    for asset in assets:
        res = optimize_parameters(strategy_id, asset, timeframe)
        if res and (best is None or res["optimal_win_rate"] > best["optimal_win_rate"]):
            best = res

    if best is None:
        return None

    db.save_optimization({
        "id": generate_id(),
        "strategy_id": strategy_id,
        "asset": best["asset"],
        "timeframe": best["timeframe"],
        "original_params": best["original_params"],
        "optimal_params": best["optimal_params"],
        "original_win_rate": best["original_win_rate"],
        "optimal_win_rate": best["optimal_win_rate"],
        "improvement_pct": best["improvement"],
        "tested_combinations": best["tested_combinations"],
        "date_optimized": today_str(),
        "applied": False,   # never auto-applied
    })
    return best
