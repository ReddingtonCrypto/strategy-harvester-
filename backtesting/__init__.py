"""
Backtesting engine (Phase 2).

Pulls historical OHLCV from Binance, computes indicators, converts a Strategy
Card's plain-English rules into testable Python expressions, simulates trades,
and produces a Backtest Report.
"""

# Importing helpers here ensures the UTF-8 console reconfigure (defined in
# utils.helpers) runs whenever any backtesting submodule is imported — even
# standalone — so emoji progress messages never crash on Windows cp1252.
from utils import helpers as _helpers  # noqa: F401
