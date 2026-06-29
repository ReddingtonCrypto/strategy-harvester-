"""
Smart-Money Engine (Phase 6).

Pure-Python, zero-LLM mechanical detection of the price-action concepts the
mentor (khaanhassan000) strategies rely on: swings/structure, liquidity sweeps,
market-structure shifts (BOS/MSS), ranges, fair-value gaps, fib zones, order
blocks, and displacement. The orchestrator (`smc_engine`) ties these into
backtestable signal functions for Range, CRT, and Textbook setups.

Runs identically in backtesting and live scanning. Every threshold is read from
config.json so it can be tuned without code changes.

Importing helpers here triggers the UTF-8 console reconfigure (Windows safety).
"""

from utils import helpers as _helpers  # noqa: F401
