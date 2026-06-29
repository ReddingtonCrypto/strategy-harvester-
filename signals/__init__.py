"""
Live signal engine (Phase 3).

Scans PASSED strategies against fresh Binance data, builds Signal objects with
market context + confluence, stores them, and tracks their outcomes. Alerting
is delegated to the `alerts` package; scheduling to the `scheduler` package.

Importing helpers here guarantees the UTF-8 console reconfigure runs whenever
any signals submodule is imported standalone (Windows cp1252 safety).
"""

from utils import helpers as _helpers  # noqa: F401

# Minutes per candle for each supported timeframe (used for expiry + outcomes).
TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080,
}


def timeframe_minutes(timeframe: str) -> int:
    """Return minutes per candle for a timeframe (default 240 = 4h)."""
    return TF_MINUTES.get((timeframe or "").lower(), 240)
