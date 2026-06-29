"""
Report generator (Phase 2).

Turns a completed trade log into a fully-populated `BacktestReport` (all
summary metrics + PASS/FAIL verdict) and prints a formatted report box to the
terminal using `rich` (with a plain-text fallback).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from models.backtest_report import BacktestReport, TradeRecord
from utils.helpers import today_str

try:
    from rich.console import Console
    from rich.panel import Panel

    _console = Console()
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _HAS_RICH = False


def _safe(value: float, ndigits: int = 2) -> float:
    """Round, coercing NaN/inf to 0.0 so the value is JSON-serialisable."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return 0.0
    return round(float(value), ndigits)


def _max_drawdown_pct(profits: list[float], starting_capital: float) -> float:
    """Largest peak-to-trough drop of the equity curve, as a percentage."""
    if not profits:
        return 0.0
    equity = starting_capital
    peak = starting_capital
    max_dd = 0.0
    for pnl in profits:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            dd = (equity - peak) / peak * 100.0
            max_dd = min(max_dd, dd)
    return _safe(max_dd)


def _sharpe(returns: list[float]) -> float:
    """Annualised Sharpe ratio: (mean/std) * sqrt(252). 0 if undefined."""
    if len(returns) < 2:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    std = arr.std(ddof=1)
    if std == 0:
        return 0.0
    return _safe((arr.mean() / std) * math.sqrt(252))


def generate_report(
    *,
    strategy_id: str,
    strategy_name: str,
    asset: str,
    timeframe: str,
    period_start: str,
    period_end: str,
    trades: list[dict[str, Any]],
    total_signals: int,
    trade_size_usd: float,
    pass_threshold: float,
) -> BacktestReport:
    """Compute all metrics from a trade log and return a BacktestReport.

    `trades` is a list of dicts each containing at least: entry_time, exit_time,
    entry_price, exit_price, profit_usd, result.
    """
    total = len(trades)
    profits_all = [float(t["profit_usd"]) for t in trades]
    winners = [p for p in profits_all if p > 0]
    losers = [p for p in profits_all if p <= 0]

    winning_trades = len(winners)
    losing_trades = len(losers)
    win_rate = _safe((winning_trades / total * 100) if total else 0.0)

    avg_profit = _safe(np.mean(winners)) if winners else 0.0
    avg_loss = _safe(np.mean(losers)) if losers else 0.0
    total_return = _safe(sum(profits_all))

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    if gross_loss > 0:
        profit_factor = _safe(gross_profit / gross_loss)
    elif gross_profit > 0:
        profit_factor = 999.99  # no losing trades
    else:
        profit_factor = 0.0

    max_drawdown = _max_drawdown_pct(profits_all, starting_capital=trade_size_usd)
    per_trade_returns = [p / trade_size_usd for p in profits_all] if trade_size_usd else []
    sharpe = _sharpe(per_trade_returns)

    # --- Verdict --------------------------------------------------------
    if total == 0:
        verdict = "FAIL"
        reason = "No trades were generated, so the strategy cannot pass."
    elif win_rate >= pass_threshold:
        verdict = "PASS"
        reason = f"Win rate {win_rate}% meets/exceeds {pass_threshold}% threshold."
    else:
        verdict = "FAIL"
        reason = f"Win rate {win_rate}% is below the {pass_threshold}% threshold."

    trade_records = [
        TradeRecord(
            entry_time=str(t.get("entry_time", "")),
            exit_time=str(t.get("exit_time", "")),
            entry_price=_safe(t.get("entry_price", 0.0), 6),
            exit_price=_safe(t.get("exit_price", 0.0), 6),
            profit_usd=_safe(t.get("profit_usd", 0.0)),
            result="WIN" if float(t.get("profit_usd", 0.0)) > 0 else "LOSS",
        )
        for t in trades
    ]

    return BacktestReport(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        asset=asset,
        timeframe=timeframe,
        period_start=period_start,
        period_end=period_end,
        total_trades=total,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        avg_profit_per_trade=avg_profit,
        avg_loss_per_trade=avg_loss,
        max_drawdown=max_drawdown,
        total_return=total_return,
        trade_size_usd=_safe(trade_size_usd),
        profit_factor=profit_factor,
        sharpe_ratio=sharpe,
        total_signals_generated=total_signals,
        verdict=verdict,
        verdict_reason=reason,
        trades_log=trade_records,
        date_run=today_str(),
        pass_threshold=pass_threshold,
    )


def print_report(report: BacktestReport) -> None:
    """Print a formatted Backtest Report box to the terminal."""
    win_emoji = "✅" if report.verdict == "PASS" else "❌"
    body = (
        f"Strategy : {report.strategy_name}\n"
        f"Period   : {report.period_start} → {report.period_end}\n"
        f"Timeframe: {report.timeframe}\n"
        f"{'-' * 38}\n"
        f"Total Trades    : {report.total_trades}\n"
        f"Win Rate        : {report.win_rate}% {win_emoji}\n"
        f"Avg Profit      : ${report.avg_profit_per_trade}\n"
        f"Avg Loss        : ${report.avg_loss_per_trade}\n"
        f"Max Drawdown    : {report.max_drawdown}%\n"
        f"Total Return    : ${report.total_return}\n"
        f"Profit Factor   : {report.profit_factor}\n"
        f"Sharpe Ratio    : {report.sharpe_ratio}\n"
        f"{'-' * 38}\n"
        f"VERDICT: {win_emoji} {report.verdict}\n"
        f"Reason : {report.verdict_reason}"
    )

    title = f"BACKTEST REPORT — {report.asset}"
    if _HAS_RICH:
        colour = "green" if report.verdict == "PASS" else "red"
        _console.print(Panel(body, title=title, border_style=colour, expand=False))
    else:
        line = "═" * 44
        print(f"\n╔{line}╗")
        print(f"  {title}")
        print(f"╠{line}╣")
        print(body)
        print(f"╚{line}╝\n")
