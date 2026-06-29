"""
Historical market-data fetcher (Phase 2).

Uses CCXT against Binance to pull OHLCV candles and caches them locally as CSV
so repeated backtests don't re-hit the API. Binance public OHLCV needs no API
key; keys (from .env) are used only to raise rate limits when present.

Public entry point:
    fetch_ohlcv(symbol, timeframe, months=12) -> pandas.DataFrame
    columns: timestamp, open, high, low, close, volume
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pandas as pd

from utils.helpers import get_env, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Approx milliseconds per candle for common timeframes (used to paginate).
_TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
}


def _cache_path(symbol: str, timeframe: str) -> Path:
    """Return the CSV cache path for a symbol/timeframe combination."""
    config = load_config()
    folder = PROJECT_ROOT / config.get("data_cache_folder", "data/cache")
    folder.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    return folder / f"{safe_symbol}_{timeframe}.csv"


# Cached CCXT client — built once and reused so each call doesn't re-run the
# heavy load_markets(). Reusing the instance also lets CCXT's rate limiter track
# request timing across the whole scan (50 coins × 3 timeframes).
_EXCHANGE = None


def _build_exchange(force_new: bool = False):
    """Return a shared, configured CCXT Binance client (keys optional)."""
    global _EXCHANGE
    if _EXCHANGE is not None and not force_new:
        return _EXCHANGE
    try:
        import ccxt
    except ImportError as exc:
        raise RuntimeError(
            "ccxt is not installed (pip install ccxt)."
        ) from exc

    params = {"enableRateLimit": True}  # CCXT auto-throttles to avoid bans.
    api_key = get_env("BINANCE_API_KEY")
    api_secret = get_env("BINANCE_API_SECRET")
    if api_key and api_secret:
        params["apiKey"] = api_key
        params["secret"] = api_secret
    _EXCHANGE = ccxt.binance(params)
    return _EXCHANGE


def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    months: int = 12,
    *,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch ~`months` of historical OHLCV candles for `symbol`.

    Parameters
    ----------
    symbol : str
        CCXT market symbol, e.g. 'BTC/USDT'.
    timeframe : str
        Candle size, e.g. '1h', '4h', '1d'.
    months : int
        How many months of history to retrieve (default 12).
    force_refresh : bool
        Ignore any cached CSV and re-download.

    Returns
    -------
    pandas.DataFrame
        Columns: timestamp (datetime), open, high, low, close, volume.
        Sorted oldest -> newest, de-duplicated.
    """
    timeframe = timeframe.lower()
    config = load_config()
    use_cache = config.get("cache_data_locally", True)
    cache_file = _cache_path(symbol, timeframe)

    # --- Try cache first ------------------------------------------------
    if use_cache and not force_refresh and cache_file.exists():
        try:
            df = pd.read_csv(cache_file, parse_dates=["timestamp"])
            if not df.empty:
                print(f"🗄️  Loaded {len(df)} cached candles from {cache_file.name}")
                return df
        except Exception as exc:  # corrupt cache shouldn't be fatal
            print(f"⚠️  Could not read cache ({exc}); re-fetching.")

    print(f"📈 Fetching {symbol} {timeframe} data (~{months} months)...")
    df = _download(symbol, timeframe, months)

    if df.empty:
        print("⚠️  No data returned from Binance.")
        return df

    # --- Save cache -----------------------------------------------------
    if use_cache:
        try:
            df.to_csv(cache_file, index=False)
            print(f"💾 Cached {len(df)} candles to {cache_file}")
        except OSError as exc:
            print(f"⚠️  Failed to write cache: {exc}")

    return df


def fetch_latest_ohlcv(
    symbol: str = "BTC/USDT", timeframe: str = "4h", limit: int = 200
) -> pd.DataFrame:
    """Fetch the most recent `limit` candles fresh (no cache) for live scanning.

    Unlike `fetch_ohlcv`, this never reads/writes the CSV cache because live
    signal scanning always needs the latest candles. Returns the same column
    layout: timestamp, open, high, low, close, volume.
    """
    timeframe = timeframe.lower()
    if timeframe not in _TF_MS:
        print(f"❌ Unsupported timeframe '{timeframe}'.")
        return _empty_ohlcv()

    try:
        exchange = _build_exchange()
        batch = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as exc:
        print(f"❌ Live fetch failed for {symbol} {timeframe}: {exc}")
        return _empty_ohlcv()

    if not batch:
        return _empty_ohlcv()

    df = pd.DataFrame(
        batch, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    return df.reset_index(drop=True)


# Known stablecoins / fiat that should never be treated as a tradeable "coin".
_STABLE_FIAT = {
    "USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "USDD", "GUSD", "PYUSD",
    "USTC", "USDS", "FRAX", "LUSD", "SUSD", "EURI", "AEUR", "EUR", "GBP",
    "AUD", "TRY", "BRL", "ARS", "UAH", "RUB", "ZAR", "NGN", "IDRT", "BIDR",
    "VAI", "USD1", "XUSD", "RLUSD", "USDE", "CRVUSD", "GHO", "USDX", "USDF",
    "USR", "USDL", "EURT", "EURS", "EURC", "USDG", "FDUSD",
}


def _looks_like_stablecoin(base: str, last_price) -> bool:
    """True for known stables, or any USD/EUR-named token trading near $1.

    Catches new stablecoins (e.g. RLUSD, USDE) without a hardcoded list — a
    real coin that merely costs ~$1 won't have USD/EUR in its symbol.
    """
    if base in _STABLE_FIAT:
        return True
    if last_price is None:
        return False
    try:
        near_one = abs(float(last_price) - 1.0) <= 0.01
    except (TypeError, ValueError):
        return False
    return near_one and any(tok in base for tok in ("USD", "EUR", "DAI"))


def fetch_top_usdt_coins(n: int = 50) -> list[str]:
    """Return the top `n` USDT spot base coins by 24h quote volume.

    Filters to /USDT spot pairs, drops stablecoins/fiat and leveraged tokens
    (UP/DOWN/BULL/BEAR), sorts by `quoteVolume` descending. Returns base symbols
    (e.g. ['BTC','ETH',...]) suitable for config `default_assets`.
    """
    try:
        exchange = _build_exchange()
        markets = exchange.load_markets()
        tickers = exchange.fetch_tickers()
    except Exception as exc:
        print(f"❌ [TopCoins] fetch failed: {exc}")
        return []

    rows: list[tuple[str, float]] = []
    for sym, t in tickers.items():
        # Must be an ACTIVE SPOT market quoted in USDT.
        m = markets.get(sym)
        if not m or not m.get("active") or not m.get("spot"):
            continue
        if m.get("quote") != "USDT":
            continue
        base = m.get("base") or ""
        # Drop junk/promo tickers (non-ASCII, non-alphanumeric symbols).
        if not base.isascii() or not base.isalnum():
            continue
        if _looks_like_stablecoin(base, (t or {}).get("last")
                                  or (t or {}).get("close")):
            continue
        if base.endswith(("UP", "DOWN", "BULL", "BEAR")):  # leveraged tokens
            continue
        qv = (t or {}).get("quoteVolume") or 0
        try:
            qv = float(qv)
        except (TypeError, ValueError):
            qv = 0.0
        if qv > 0:
            rows.append((base, qv))

    rows.sort(key=lambda x: x[1], reverse=True)
    top = [b for b, _ in rows[:n]]
    print(f"📊 [TopCoins] {len(top)} USDT spot coins by 24h volume "
          f"(of {len(rows)} eligible pairs).")
    return top


def get_current_price(symbol: str = "BTC/USDT") -> Optional[float]:
    """Return the latest traded price for `symbol`, or None on failure."""
    try:
        exchange = _build_exchange()
        ticker = exchange.fetch_ticker(symbol)
        price = ticker.get("last") or ticker.get("close")
        return float(price) if price is not None else None
    except Exception as exc:
        print(f"❌ Could not fetch current price for {symbol}: {exc}")
        return None


def _empty_ohlcv() -> pd.DataFrame:
    """Return an empty OHLCV DataFrame with the standard columns."""
    return pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )


def _download(symbol: str, timeframe: str, months: int) -> pd.DataFrame:
    """Page through Binance OHLCV from `months` ago until now."""
    exchange = _build_exchange()

    tf_ms = _TF_MS.get(timeframe)
    if tf_ms is None:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. "
            f"Choose one of: {', '.join(_TF_MS)}"
        )

    now_ms = exchange.milliseconds()
    # Approximate a month as 30 days for the lookback window.
    since = now_ms - months * 30 * 86_400_000
    limit = 1000  # Binance max candles per request.

    all_rows: list[list] = []
    safety_counter = 0
    max_pages = months * 31 * 86_400_000 // (tf_ms * limit) + 5

    while since < now_ms and safety_counter < max_pages:
        safety_counter += 1
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as exc:
            print(f"❌ Binance fetch failed: {exc}")
            # Give the API a moment, then stop rather than loop forever.
            time.sleep(1.0)
            break

        if not batch:
            break

        all_rows.extend(batch)
        last_ts = batch[-1][0]
        # Advance just past the last candle to avoid duplicates.
        since = last_ts + tf_ms

        print(f"  ...fetched {len(all_rows)} candles so far", end="\r")

        # Be polite even though enableRateLimit already throttles.
        time.sleep(0.2)

        if len(batch) < limit:
            break  # Reached the most recent candle.

    print()  # newline after the progress line
    if not all_rows:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    df = pd.DataFrame(
        all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    df = df.reset_index(drop=True)
    print(f"✅ Downloaded {len(df)} candles "
          f"({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}).")
    return df
