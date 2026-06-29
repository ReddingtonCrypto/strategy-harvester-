"""
Indicator engine (Phase 2).

Adds technical-indicator columns to an OHLCV DataFrame using a fixed,
canonical naming scheme that the rule parser/backtester rely on:

    rsi_14
    sma_20, sma_50, sma_200
    ema_20, ema_50, ema_200
    macd, macd_signal, macd_hist
    bb_upper, bb_mid, bb_lower
    adx_14
    volume_sma_20
    stochrsi_k, stochrsi_d

Implementation note
-------------------
The project spec calls for `pandas-ta`. That library cannot be installed on
Python 3.14 (its `numba` dependency caps at <3.14), so this module ships
self-contained pandas/numpy implementations that produce the same standard
indicator values. If `pandas-ta` *is* importable (older Python), it is detected
but the canonical columns above are still what downstream code uses.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Detect pandas-ta purely for an informational message; the native
# implementations below are always used so column names stay canonical.
try:  # pragma: no cover - environment dependent
    import pandas_ta  # noqa: F401
    _HAS_PANDAS_TA = True
except Exception:
    _HAS_PANDAS_TA = False

# Map loose indicator names (as they appear on a Strategy Card) to the
# canonical column(s) this engine produces. Used only for warn-on-unknown.
_KNOWN_ALIASES = {
    "rsi": ["rsi_14"],
    "sma": ["sma_20", "sma_50", "sma_200"],
    "ma": ["sma_20", "sma_50", "sma_200"],
    "ma200": ["sma_200"],
    "ma50": ["sma_50"],
    "ma20": ["sma_20"],
    "ema": ["ema_20", "ema_50", "ema_200"],
    "macd": ["macd", "macd_signal", "macd_hist"],
    "bollinger": ["bb_upper", "bb_mid", "bb_lower"],
    "bollingerbands": ["bb_upper", "bb_mid", "bb_lower"],
    "bb": ["bb_upper", "bb_mid", "bb_lower"],
    "adx": ["adx_14"],
    "volume": ["volume_sma_20"],
    "volumesma": ["volume_sma_20"],
    "stochrsi": ["stochrsi_k", "stochrsi_d"],
    "stochasticrsi": ["stochrsi_k", "stochrsi_d"],
}


# --- Individual indicator calculations ----------------------------------

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    """Return (upper, mid, lower) Bollinger Bands."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index (Wilder)."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    prev_close = close.shift()
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    alpha = 1 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _stoch_rsi(close: pd.Series, period: int = 14, k: int = 3, d: int = 3):
    """Stochastic RSI: return (%K, %D) scaled 0-100."""
    rsi = _rsi(close, period)
    lowest = rsi.rolling(period).min()
    highest = rsi.rolling(period).max()
    stoch = (rsi - lowest) / (highest - lowest).replace(0, np.nan) * 100
    k_line = stoch.rolling(k).mean()
    d_line = k_line.rolling(d).mean()
    return k_line, d_line


# --- Public API ----------------------------------------------------------

def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add the full canonical set of indicator columns to `df`.

    Returns a new DataFrame (the input is not mutated).
    """
    out = df.copy()
    close = out["close"]

    out["rsi_14"] = _rsi(close, 14)

    for p in (20, 50, 200):
        out[f"sma_{p}"] = close.rolling(p).mean()
        out[f"ema_{p}"] = close.ewm(span=p, adjust=False).mean()

    out["macd"], out["macd_signal"], out["macd_hist"] = _macd(close)
    out["bb_upper"], out["bb_mid"], out["bb_lower"] = _bollinger(close)
    out["adx_14"] = _adx(out)

    if "volume" in out.columns:
        out["volume_sma_20"] = out["volume"].rolling(20).mean()

    out["stochrsi_k"], out["stochrsi_d"] = _stoch_rsi(close)

    return out


def calculate_indicators(
    df: pd.DataFrame, indicators_list: list[str] | None = None
) -> pd.DataFrame:
    """Compute indicators for a DataFrame.

    The full canonical indicator set is always added (so rule expressions can
    reference any supported variable). `indicators_list` — the indicator names
    from a Strategy Card — is used only to report which were recognised and to
    warn about unknown ones.

    Parameters
    ----------
    df : pandas.DataFrame
        OHLCV data with at least a 'close' column.
    indicators_list : list[str] | None
        Indicator names from the Strategy Card, e.g. ['RSI', 'MA200'].

    Returns
    -------
    pandas.DataFrame
        Input columns plus all canonical indicator columns.
    """
    if df is None or df.empty or "close" not in df.columns:
        print("⚠️  [Indicators] DataFrame is empty or missing 'close'.")
        return df

    engine = "pandas-ta detected" if _HAS_PANDAS_TA else "built-in pandas"
    print(f"🧮 [Indicators] Calculating indicators ({engine})...")

    out = calculate_all_indicators(df)

    # Validate the requested names purely for user feedback.
    if indicators_list:
        for name in indicators_list:
            key = "".join(ch for ch in str(name).lower() if ch.isalnum())
            if key in _KNOWN_ALIASES:
                print(f"  ✓ recognised indicator: {name}")
            else:
                print(f"  ⚠️  unknown indicator '{name}' — skipped "
                      f"(canonical set still computed).")

    print(f"✅ [Indicators] Added {out.shape[1] - df.shape[1]} indicator columns.")
    return out
