"""
Performance tracker + auto trust scoring (Phase 5.5, Part 2).

Pure-Python + DB: computes a strategy's win rates across every category from
its recorded signal outcomes, derives a 0-100 trust score, and reports the
conditions under which the strategy historically performs best. The live
scanner uses these to only fire strategies where they actually work.
"""

from __future__ import annotations

from typing import Any, Optional

from signals import signal_store
from storage import database as db
from utils.helpers import load_config, today_str

_ASSETS = ["BTC", "ETH", "SOL", "BNB"]
_TIMEFRAMES = ["1H", "4H", "1D"]
_MARKETS = ["UPTREND", "DOWNTREND", "SIDEWAYS"]


def _wr(signals: list) -> Optional[float]:
    """Win rate (%) for a list of decided signals, or None if no samples."""
    wins = sum(1 for s in signals if s.outcome_result == "WIN")
    losses = sum(1 for s in signals if s.outcome_result == "LOSS")
    decided = wins + losses
    return round(wins / decided * 100, 1) if decided else None


def _avg_conf(signals: list, result: str) -> float:
    vals = [s.confidence_score for s in signals if s.outcome_result == result]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def _all_signals(strategy_id: str) -> list:
    return [s for s in signal_store.get_history(per_page=100000)
            if s.strategy_id == strategy_id]


def update_performance(strategy_id: str) -> Optional[dict[str, Any]]:
    """Recompute and persist a strategy's full performance + trust score."""
    from storage import strategy_store

    card = strategy_store.get_card(strategy_id)
    if card is None:
        return None

    sigs = _all_signals(strategy_id)
    decided = [s for s in sigs if s.outcome_result in ("WIN", "LOSS")]
    if not decided:
        print(f"ℹ️  [Performance] No decided outcomes for {card.name} yet.")
        return None

    by_asset = {a: _wr([s for s in decided
                        if s.asset.split("/")[0].upper() == a]) for a in _ASSETS}
    by_tf = {tf: _wr([s for s in decided if s.timeframe.upper() == tf])
             for tf in _TIMEFRAMES}
    by_market = {m: _wr([s for s in decided if s.market_trend == m])
                 for m in _MARKETS}
    vol_conf = _wr([s for s in decided if s.volume_confirmation])
    no_vol = _wr([s for s in decided if not s.volume_confirmation])
    strong = _wr([s for s in decided if s.trend_strength == "STRONG"])
    weak = _wr([s for s in decided if s.trend_strength == "WEAK"])
    overall = _wr(decided) or 0.0

    best_asset = _best(by_asset)
    best_tf = _best(by_tf)
    best_market = _best(by_market)
    avoid_condition = _worst(by_market)

    trust = _trust_score(overall, vol_conf, no_vol, strong, weak,
                         len(decided), by_asset)

    perf = {
        "strategy_id": strategy_id,
        "strategy_name": card.name,
        "version": card.version,
        "total_signals": len(sigs),
        "total_outcomes": len(decided),
        "win_rate_overall": overall,
        "win_rate_btc": by_asset["BTC"], "win_rate_eth": by_asset["ETH"],
        "win_rate_sol": by_asset["SOL"], "win_rate_bnb": by_asset["BNB"],
        "win_rate_1h": by_tf["1H"], "win_rate_4h": by_tf["4H"],
        "win_rate_1d": by_tf["1D"],
        "win_rate_uptrend": by_market["UPTREND"],
        "win_rate_downtrend": by_market["DOWNTREND"],
        "win_rate_sideways": by_market["SIDEWAYS"],
        "win_rate_volume_confirmed": vol_conf, "win_rate_no_volume": no_vol,
        "win_rate_strong_trend": strong, "win_rate_weak_trend": weak,
        "avg_confidence_winners": _avg_conf(decided, "WIN"),
        "avg_confidence_losers": _avg_conf(decided, "LOSS"),
        "best_asset": best_asset, "best_timeframe": best_tf,
        "best_market_condition": best_market, "avoid_condition": avoid_condition,
        "trust_score": trust, "last_updated": today_str(),
    }
    db.save_performance(perf)
    print(f"⭐ [Performance] {card.name} v{card.version}: "
          f"win {overall}% | trust {trust} | {len(decided)} outcomes.")
    return perf


def _best(mapping: dict[str, Optional[float]]) -> Optional[str]:
    """Key with the highest non-None value."""
    valid = {k: v for k, v in mapping.items() if v is not None}
    return max(valid, key=valid.get) if valid else None


def _worst(mapping: dict[str, Optional[float]]) -> Optional[str]:
    """Key with the lowest non-None value."""
    valid = {k: v for k, v in mapping.items() if v is not None}
    return min(valid, key=valid.get) if valid else None


def _trust_score(overall: float, vol_conf: Optional[float],
                 no_vol: Optional[float], strong: Optional[float],
                 weak: Optional[float], n_outcomes: int,
                 by_asset: dict[str, Optional[float]]) -> int:
    """Compute the 0-100 trust score per the spec formula."""
    score = min(40.0, overall * 0.4)  # base (max 40)

    if vol_conf is not None and no_vol is not None:  # volume bonus (max 15)
        score += max(0.0, min(15.0, (vol_conf - no_vol) / 2))
    if strong is not None and weak is not None:  # trend bonus (max 15)
        score += max(0.0, min(15.0, (strong - weak) / 2))

    score += min(n_outcomes / 50 * 20, 20)  # sample-size bonus (max 20)

    asset_rates = [v for v in by_asset.values() if v is not None]
    if len(asset_rates) >= 2 and (max(asset_rates) - min(asset_rates)) < 20:
        score += 10  # consistency bonus

    return int(max(0, min(100, round(score))))


def get_trust_score(strategy_id: str) -> int:
    """Return the strategy's trust score, or 50 (neutral) if unknown."""
    perf = db.get_performance(strategy_id)
    return int(perf["trust_score"]) if perf and perf.get("trust_score") is not None \
        else 50


def get_best_conditions(strategy_id: str) -> Optional[dict[str, Any]]:
    """Return the conditions a strategy works best in, or None if not enough data.

    None means "don't filter" (e.g. a new strategy). The scanner only restricts
    a strategy to its best conditions once it has enough outcomes.
    """
    perf = db.get_performance(strategy_id)
    if not perf:
        return None
    min_n = int(load_config().get("min_signals_for_analysis", 10))
    if (perf.get("total_outcomes") or 0) < min_n:
        return None

    overall = perf.get("win_rate_overall") or 0.0
    asset_map = {"BTC": perf.get("win_rate_btc"), "ETH": perf.get("win_rate_eth"),
                 "SOL": perf.get("win_rate_sol"), "BNB": perf.get("win_rate_bnb")}
    tf_map = {"1H": perf.get("win_rate_1h"), "4H": perf.get("win_rate_4h"),
              "1D": perf.get("win_rate_1d")}

    best_assets = [a for a, v in asset_map.items()
                   if v is not None and v >= overall]
    best_timeframes = [t for t, v in tf_map.items()
                       if v is not None and v >= overall]

    vol_conf = perf.get("win_rate_volume_confirmed")
    no_vol = perf.get("win_rate_no_volume")
    require_volume = (vol_conf is not None and no_vol is not None
                      and vol_conf > no_vol + 10)

    strong = perf.get("win_rate_strong_trend")
    weak = perf.get("win_rate_weak_trend")
    min_adx = 20 if (strong is not None and weak is not None
                     and strong > weak + 10) else 0

    return {
        "best_assets": best_assets,
        "best_timeframes": best_timeframes,
        "best_market": perf.get("best_market_condition"),
        "avoid_market": perf.get("avoid_condition"),
        "require_volume": require_volume,
        "min_adx": min_adx,
    }
