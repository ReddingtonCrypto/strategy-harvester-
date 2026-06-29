"""
Backtest runner (Phase 2).

Orchestrates a full backtest for one Strategy Card against one asset/timeframe:

    1. Fetch ~12 months of OHLCV from Binance (cached).
    2. Calculate all indicators.
    3. Parse the strategy's rules into Python expressions (cached on the card).
    4. Simulate trades candle-by-candle (one position at a time, $100/trade,
       0.1% fee per side).
    5. Compute statistics and build a Backtest Report.
    6. Print it and save it back onto the Strategy Card in SQLite.

The runner is defensive: if rule parsing fails it falls back to simple RSI
rules, and any per-candle evaluation error is treated as "condition false"
rather than crashing the run.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from backtesting import data_fetcher, indicator_engine, report_generator
from backtesting.rule_parser import parse_strategy_rules
from models.backtest_report import BacktestReport
from models.strategy_card import StrategyCard
from storage import strategy_store
from utils.helpers import load_config


def _normalise_symbol(asset: str) -> str:
    """Turn 'BTC' into 'BTC/USDT'; leave full symbols like 'ETH/USDT' alone."""
    asset = (asset or "").strip().upper()
    if not asset:
        return "BTC/USDT"
    return asset if "/" in asset else f"{asset}/USDT"


def _ts(value: Any) -> str:
    """Format a pandas timestamp as 'YYYY-MM-DD HH:MM'."""
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def run_backtest(
    strategy_card: StrategyCard,
    asset: str = "BTC/USDT",
    timeframe: Optional[str] = None,
    *,
    df: Optional[pd.DataFrame] = None,
    save: bool = True,
) -> BacktestReport:
    """Run a full backtest and return a BacktestReport.

    Parameters
    ----------
    strategy_card : StrategyCard
        The (ideally approved) strategy to test.
    asset : str
        Symbol like 'BTC/USDT' or a bare 'BTC' (→ 'BTC/USDT').
    timeframe : str | None
        Candle size; defaults to the card's timeframe or '4h'.
    df : pandas.DataFrame | None
        Optional pre-supplied OHLCV data (used mainly for testing); when given,
        the Binance fetch step is skipped.
    save : bool
        Persist parsed rules + the report back onto the Strategy Card.
    """
    config = load_config()
    symbol = _normalise_symbol(asset)
    timeframe = (timeframe or strategy_card.timeframe or "4h").lower()
    months = int(config.get("backtest_months", 12))
    trade_size = float(config.get("trade_size_usd", 100))
    fee_frac = float(config.get("binance_fee_percent", 0.1)) / 100.0
    threshold = float(config.get("win_rate_threshold", 50))

    print(f"\n🚀 [Backtest] {strategy_card.name} on {symbol} {timeframe} "
          f"(~{months} months)")

    # --- Step 1: data ---------------------------------------------------
    if df is None:
        print("Step 1/6: Fetching historical data...")
        df = data_fetcher.fetch_ohlcv(symbol, timeframe, months=months)
    else:
        print("Step 1/6: Using supplied DataFrame (skipping fetch).")

    if df is None or df.empty or len(df) < 2:
        print("❌ [Backtest] Not enough data to run a backtest.")
        return _empty_report(strategy_card, symbol, timeframe, threshold, trade_size,
                             reason="No/insufficient historical data was available.")

    # --- Step 2: indicators ---------------------------------------------
    print("Step 2/6: Calculating indicators...")
    df = indicator_engine.calculate_indicators(df, strategy_card.indicators)

    # --- Step 3/4: Smart-Money engine OR generic rules -----------------
    if getattr(strategy_card, "engine", "generic") == "smc":
        print(f"Step 3/6: Smart-Money engine ('{strategy_card.engine_signal}')...")
        from smart_money import smc_engine
        entries = smc_engine.get_entries(df, strategy_card.engine_signal)
        print(f"   {len(entries)} {strategy_card.engine_signal} entries detected.")
        print("Step 4/6: Simulating trades (target/stop exits)...")
        trades = _simulate_smc(df, entries, trade_size, fee_frac)
    else:
        print("Step 3/6: Parsing strategy rules...")
        rules = parse_strategy_rules(strategy_card)
        entry_rule, exit_rule = rules["entry_rule"], rules["exit_rule"]
        print(f"   entry: {entry_rule}")
        print(f"   exit : {exit_rule}")

        # Persist parsed rules onto the card so we don't re-parse next time.
        strategy_card.entry_rule = entry_rule
        strategy_card.exit_rule = exit_rule
        strategy_card.rule_notes = rules.get("notes", "")
        if save:
            strategy_store.update_card(
                strategy_card.id,
                {"entry_rule": entry_rule, "exit_rule": exit_rule,
                 "rule_notes": strategy_card.rule_notes},
            )

        print("Step 4/6: Simulating trades...")
        trades = _simulate(df, entry_rule, exit_rule, trade_size, fee_frac)
    print(f"   {len(trades)} trades simulated.")

    # --- Step 5/6: report ----------------------------------------------
    print("Step 5/6: Building report...")
    report = report_generator.generate_report(
        strategy_id=strategy_card.id,
        strategy_name=strategy_card.name,
        asset=symbol,
        timeframe=timeframe,
        period_start=_ts(df["timestamp"].iloc[0]),
        period_end=_ts(df["timestamp"].iloc[-1]),
        trades=trades,
        total_signals=len(trades),
        trade_size_usd=trade_size,
        pass_threshold=threshold,
    )

    report_generator.print_report(report)

    print("Step 6/6: Saving report to database...")
    if save:
        strategy_store.update_card(
            strategy_card.id, {"backtest_result": report.to_dict()}
        )
        print("✅ [Backtest] Report saved to Strategy Card.")

    return report


# --- Simulation core -----------------------------------------------------

def _simulate(
    df: pd.DataFrame,
    entry_rule: str,
    exit_rule: str,
    trade_size: float,
    fee_frac: float,
) -> list[dict[str, Any]]:
    """Scan candles forward and record completed trades.

    One position at a time. Enters when `entry_rule` is True and flat; exits
    when `exit_rule` is True and in a position. Any open position at the end is
    closed on the final candle so its result is realised.
    """
    try:
        entry_code = compile(entry_rule, "<entry_rule>", "eval")
        exit_code = compile(exit_rule, "<exit_rule>", "eval")
    except SyntaxError as exc:
        print(f"❌ [Backtest] Could not compile rules ({exc}); no trades.")
        return []

    safe_globals = {"__builtins__": {}}
    trades: list[dict[str, Any]] = []
    in_position = False
    entry_price = 0.0
    entry_time: Any = None
    eval_errors = 0

    rows = df.reset_index(drop=True)
    n = len(rows)

    for i in range(1, n):
        cur = rows.iloc[i]
        prev = rows.iloc[i - 1]
        scope = {"df": cur, "prev": prev}

        try:
            if not in_position:
                if bool(eval(entry_code, safe_globals, scope)):
                    in_position = True
                    entry_price = float(cur["close"])
                    entry_time = cur["timestamp"]
            else:
                if bool(eval(exit_code, safe_globals, scope)):
                    trades.append(
                        _close_trade(entry_time, cur["timestamp"], entry_price,
                                     float(cur["close"]), trade_size, fee_frac)
                    )
                    in_position = False
        except Exception:
            eval_errors += 1
            continue

    # Close any still-open position on the last candle.
    if in_position:
        last = rows.iloc[-1]
        trades.append(
            _close_trade(entry_time, last["timestamp"], entry_price,
                         float(last["close"]), trade_size, fee_frac)
        )

    if eval_errors:
        print(f"   ⚠️  {eval_errors} candle(s) skipped due to evaluation errors.")
    return trades


def _simulate_smc(df: pd.DataFrame, entries: list[dict[str, Any]],
                  trade_size: float, fee_frac: float) -> list[dict[str, Any]]:
    """Simulate Smart-Money LONG entries with target/stop exits.

    One position at a time. Enter at the signal candle's close; exit when a
    later candle hits the stop (checked first, conservative) or the target,
    else close on the final candle. Reuses `_close_trade` for sizing/fees.
    """
    rows = df.reset_index(drop=True)
    n = len(rows)
    high = rows["high"].to_numpy(dtype=float)
    low = rows["low"].to_numpy(dtype=float)
    trades: list[dict[str, Any]] = []
    last_exit_i = -1

    for e in sorted(entries, key=lambda x: x["index"]):
        ei = int(e["index"])
        if ei <= last_exit_i or ei >= n - 1:
            continue
        entry = float(e["entry"])
        target = float(e["target"])
        stop = float(e["stop"])
        # valid long only: stop below entry below target
        if not (stop < entry < target):
            continue

        exit_i, exit_price = None, None
        for j in range(ei + 1, n):
            if low[j] <= stop:
                exit_i, exit_price = j, stop
                break
            if high[j] >= target:
                exit_i, exit_price = j, target
                break
        if exit_i is None:
            exit_i, exit_price = n - 1, float(rows["close"].iat[-1])

        trades.append(_close_trade(
            rows["timestamp"].iat[ei], rows["timestamp"].iat[exit_i],
            entry, exit_price, trade_size, fee_frac))
        last_exit_i = exit_i
    return trades


def _close_trade(entry_time, exit_time, entry_price, exit_price,
                 trade_size, fee_frac) -> dict[str, Any]:
    """Compute the P&L for one completed long trade (fees on both sides)."""
    qty = trade_size / entry_price if entry_price else 0.0
    gross_exit = qty * exit_price
    entry_fee = trade_size * fee_frac
    exit_fee = gross_exit * fee_frac
    profit = (gross_exit - trade_size) - entry_fee - exit_fee
    return {
        "entry_time": _ts(entry_time),
        "exit_time": _ts(exit_time),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "profit_usd": profit,
        "result": "WIN" if profit > 0 else "LOSS",
    }


def _empty_report(card: StrategyCard, symbol: str, timeframe: str,
                  threshold: float, trade_size: float, reason: str) -> BacktestReport:
    """Build a FAIL report when a run cannot proceed (e.g. no data)."""
    report = report_generator.generate_report(
        strategy_id=card.id,
        strategy_name=card.name,
        asset=symbol,
        timeframe=timeframe,
        period_start="",
        period_end="",
        trades=[],
        total_signals=0,
        trade_size_usd=trade_size,
        pass_threshold=threshold,
    )
    report.verdict_reason = reason
    return report
