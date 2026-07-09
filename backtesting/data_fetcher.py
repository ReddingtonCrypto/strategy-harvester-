"""
Historical market-data fetcher (Phase 2).

Uses CCXT to pull OHLCV candles and caches them locally as CSV so repeated
backtests don't re-hit the API. Public spot OHLCV needs no API key.

Exchange selection
------------------
The data source is config-driven (`data_exchange`, default "binance"). We trade
on **binance.com (global)**, so we want signals/backtests measured against that
same venue. The catch: binance.com's main API (api.binance.com) returns HTTP 451
from US IPs (GitHub Actions runners) and some regions. Fix (proven in the
sibling `crypto-agent` project, 2026-07-07): Binance's PUBLIC market-data host
`data-api.binance.vision` serves the same global spot data — real prices/wicks,
real global volume, the full pair list — and IS reachable from US IPs, so both
local runs and the cloud scanner see identical binance.com data. `_make_client()`
patches `urls["api"]["public"]` to that host for `name == "binance"`.

If binance is geo-blocked or unreachable anyway, we fall through a configurable
chain (`data_exchange_fallbacks`: bybit → okx → kucoin → kraken) and use the
first one that actually returns data.

All exchanges are used in **spot** mode with CCXT's unified BTC/USDT symbols, so
the top-50 picker and the candle fetcher always agree on what's fetchable.

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
    """Return the CSV cache path for a symbol/timeframe/exchange combination.

    Tagged with the configured primary exchange so switching data sources
    (e.g. bybit -> binance) can never silently serve stale candles from the
    old venue under the same filename — each source gets its own cache file.
    """
    config = load_config()
    folder = PROJECT_ROOT / config.get("data_cache_folder", "data/cache")
    folder.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    return folder / f"{safe_symbol}_{timeframe}_{configured_primary()}.csv"


# Cached CCXT client — built once and reused so each call doesn't re-run the
# heavy load_markets(). Reusing the instance also lets CCXT's rate limiter track
# request timing across the whole scan (50 coins × 3 timeframes).
_EXCHANGE = None
_EXCHANGE_NAME = None

# Fallback order if binance (primary) is unreachable. These are only used as a
# backup — binance.com via the vision host (see _make_client) is preferred so
# signals/backtests match the venue we actually trade on.
_DEFAULT_FALLBACKS = ["bybit", "okx", "kucoin", "kraken"]

# Alternate (mirror) hostnames to try when an exchange's primary domain is
# unreachable. Bybit's api.bybit.com is DNS-blocked on some ISPs/regions; its
# official api.bytick.com mirror serves the same data and resolves everywhere.
# Binance's public-data mirror (data-api.binance.vision) is handled separately
# in _make_client() since it only swaps the "public" URL bucket, not the whole
# client (private/sapi endpoints stay on api.binance.com, unused here anyway).
_MIRROR_HOSTNAMES = {"bybit": ["bytick.com"]}


def _geo_blocked(exc: Exception) -> bool:
    """True if the error looks like a geo-block / region restriction (HTTP 451)."""
    msg = str(exc).lower()
    return ("451" in msg or "restricted location" in msg
            or "eligibility" in msg or "not available in" in msg
            or "service unavailable from a restricted" in msg)


def active_source() -> Optional[str]:
    """The label of the currently-selected source, e.g. 'bybit@bytick.com'.

    None until the first fetch builds an exchange. Use `active_exchange_id()`
    for the bare exchange (mirror host stripped).
    """
    return _EXCHANGE_NAME


def active_exchange_id() -> Optional[str]:
    """The bare exchange id of the active source ('bybit' for 'bybit@bytick.com')."""
    return _EXCHANGE_NAME.split("@")[0] if _EXCHANGE_NAME else None


def configured_primary() -> str:
    """The primary exchange: env DATA_EXCHANGE wins, else config (default binance).

    The env override lets the cloud (GitHub Actions) force a different exchange
    without touching config.json, e.g. if the binance.com vision host ever has
    an outage — fall back to OKX/Bybit there while local keeps using binance.
    """
    import os
    return str(os.environ.get("DATA_EXCHANGE")
               or load_config().get("data_exchange", "binance")).lower()


def _candidate_exchanges() -> list[str]:
    """Ordered, de-duplicated list of exchanges to try (primary first)."""
    config = load_config()
    primary = configured_primary()  # honors the DATA_EXCHANGE env override
    fallbacks = config.get("data_exchange_fallbacks", _DEFAULT_FALLBACKS)
    chain = [primary] + [str(x).lower() for x in fallbacks]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in chain:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _make_client(name: str, hostname: Optional[str] = None):
    """Construct a single CCXT spot client by exchange id (no verification).

    `hostname` optionally overrides the API domain (e.g. Bybit's bytick mirror).
    """
    import ccxt

    if not hasattr(ccxt, name):
        raise ValueError(f"Unknown CCXT exchange '{name}'.")
    params = {
        "enableRateLimit": True,        # CCXT auto-throttles to avoid bans.
        "options": {"defaultType": "spot"},  # spot candles only — no perps.
    }
    if hostname:
        params["hostname"] = hostname   # CCXT rebuilds api URLs from {hostname}.
    # API keys only help on Binance (higher rate limits); other exchanges need
    # no key for public spot OHLCV.
    if name == "binance":
        api_key = get_env("BINANCE_API_KEY")
        api_secret = get_env("BINANCE_API_SECRET")
        if api_key and api_secret:
            params["apiKey"] = api_key
            params["secret"] = api_secret
    client = getattr(ccxt, name)(params)
    if name == "binance" and hostname is None:
        # Route public market-data (klines/exchangeInfo/tickers) through
        # binance's US-reachable vision mirror — api.binance.com itself 451s
        # from US/cloud IPs. Only the "public" bucket is repointed; we never
        # use private/sapi endpoints (read-only OHLCV only, never trades).
        client.urls["api"]["public"] = "https://data-api.binance.vision/api/v3"
    return client


def _host_attempts(name: str) -> list[Optional[str]]:
    """Hostnames to try for `name`: the default (None) then any mirrors."""
    config = load_config()
    overrides = config.get("data_exchange_hostnames", {}) or {}
    mirrors = overrides.get(name, _MIRROR_HOSTNAMES.get(name, []))
    return [None] + [str(h) for h in mirrors]


def _build_exchange(force_new: bool = False):
    """Return a shared CCXT spot client, falling through geo-blocks.

    Tries each exchange in `_candidate_exchanges()`, verifying reachability with
    load_markets() (where a 451 geo-block surfaces). Caches the first that works.
    """
    global _EXCHANGE, _EXCHANGE_NAME
    if _EXCHANGE is not None and not force_new:
        return _EXCHANGE

    try:
        import ccxt  # noqa: F401 — ensure the dependency exists up front
    except ImportError as exc:
        raise RuntimeError("ccxt is not installed (pip install ccxt).") from exc

    errors: list[str] = []
    for name in _candidate_exchanges():
        for host in _host_attempts(name):
            label = name if host is None else f"{name}@{host}"
            try:
                client = _make_client(name, host)
                client.load_markets()  # geo-block / network errors surface here
            except Exception as exc:  # noqa: BLE001 — try next host/exchange
                tag = "geo-blocked" if _geo_blocked(exc) else "unavailable"
                short = str(exc).replace("\n", " ")[:90]
                print(f"⚠️  [Data] {label} {tag} ({short}); trying next source...")
                errors.append(f"{label}: {short}")
                continue
            _EXCHANGE = client
            _EXCHANGE_NAME = label
            print(f"🌐 [Data] Market-data source: {label} (spot)")
            return _EXCHANGE

    raise RuntimeError(
        "No usable market-data exchange. Tried " + " | ".join(errors))


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
        print(f"⚠️  No data returned from {_EXCHANGE_NAME or 'exchange'}.")
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
    """Page through the active exchange's OHLCV from `months` ago until now."""
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
    limit = 1000  # Per-request candle cap (Bybit/Binance/OKX all allow 1000).

    all_rows: list[list] = []
    safety_counter = 0
    max_pages = months * 31 * 86_400_000 // (tf_ms * limit) + 5

    while since < now_ms and safety_counter < max_pages:
        safety_counter += 1
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as exc:
            print(f"❌ {_EXCHANGE_NAME or 'Exchange'} fetch failed: {exc}")
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

        # Stop only once we've actually reached the present. We can't use
        # `len(batch) < limit` as the end-of-history signal: Bybit (and others)
        # return ~999 per page even mid-history, which would truncate the year.
        # The `since >= now_ms` loop guard plus the empty-batch break above are
        # the real termination; this just avoids a redundant final request once
        # the newest candle is within one bar of now.
        if last_ts + tf_ms >= now_ms:
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
