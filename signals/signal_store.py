"""
Signal storage + outcome tracking (Phase 3).

Owns all reads/writes to the `signals` table and the outcome-tracking logic
that feeds Phase 4 learning. Uses the shared connection from storage.database.
All timestamps are UTC strings ('YYYY-MM-DD HH:MM:SS').
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any, Optional

from models.signal import Signal
from signals import timeframe_minutes
from storage.database import get_connection
from utils.helpers import load_config, parse_utc, utc_now, utc_now_str

# Column order for the signals table (matches the schema in database.py).
_COLUMNS = [
    "id", "strategy_id", "strategy_name", "asset", "timeframe", "signal_type",
    "entry_zone_low", "entry_zone_high", "current_price",
    "entry_price_at_signal", "target_price", "stop_price",
    "confidence_score", "market_trend",
    "trend_strength", "volume_confirmation", "confluence_count",
    "confluence_strategies", "source", "timeframe_alignment", "signal_status",
    "date_generated", "date_expires", "alerted", "alert_sent_at",
    "outcome_1h", "outcome_4h", "outcome_24h", "outcome_result",
    "outcome_pct_move", "mode",
]
_JSON_FIELDS = {"confluence_strategies"}
_BOOL_FIELDS = {"volume_confirmation", "timeframe_alignment", "alerted"}


# --- (de)serialisation ---------------------------------------------------

def _encode(signal: dict[str, Any]) -> dict[str, Any]:
    """Convert a Signal dict into DB-storable primitives."""
    row = dict(signal)
    for field in _JSON_FIELDS:
        if field in row:
            row[field] = json.dumps(row[field] or [])
    for field in _BOOL_FIELDS:
        if field in row:
            row[field] = 1 if row[field] else 0
    return row


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a DB row back into a Signal dict."""
    data = dict(row)
    for field in _JSON_FIELDS:
        if data.get(field) is not None:
            try:
                data[field] = json.loads(data[field])
            except (json.JSONDecodeError, TypeError):
                data[field] = []
    for field in _BOOL_FIELDS:
        if field in data and data[field] is not None:
            data[field] = bool(data[field])
    return data


# --- Expiry helper -------------------------------------------------------

def compute_expiry(date_generated: str, timeframe: str, candles: int) -> str:
    """Return the UTC expiry timestamp = generated + candles * timeframe."""
    start = parse_utc(date_generated) or utc_now()
    minutes = timeframe_minutes(timeframe) * max(1, candles)
    return (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


# --- CRUD ----------------------------------------------------------------

def save_signal(signal: Signal | dict[str, Any]) -> None:
    """Insert or replace a signal (keyed by id)."""
    data = signal.to_dict() if isinstance(signal, Signal) else dict(signal)
    row = _encode(data)
    values = [row.get(col) for col in _COLUMNS]
    placeholders = ", ".join("?" for _ in _COLUMNS)
    try:
        with get_connection() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO signals "
                f"({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(f"❌ Failed to save signal '{data.get('id')}': {exc}")


def get_signal(signal_id: str) -> Optional[Signal]:
    """Return a single Signal by id, or None."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM signals WHERE id = ?", (signal_id,)
            ).fetchone()
        return Signal.from_dict(_decode(row)) if row else None
    except sqlite3.Error as exc:
        print(f"❌ Failed to read signal '{signal_id}': {exc}")
        return None


def _query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run a SELECT and return decoded signal dicts."""
    try:
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"❌ Signal query failed: {exc}")
        return []


def is_duplicate(strategy_id: str, asset: str, signal_type: str,
                 timeframe: Optional[str] = None, candles: int = 3) -> bool:
    """Return True if the same signal is already ACTIVE (fired recently).

    `expire_old_signals` marks signals EXPIRED once they pass their
    `date_expires` (generated + `signal_expiry_candles` candles), so an ACTIVE
    match means the same signal fired within the last few candles — exactly the
    spam-prevention window the spec asks for.
    """
    sql = ("SELECT COUNT(*) AS n FROM signals WHERE strategy_id = ? AND "
           "asset = ? AND signal_type = ? AND signal_status = 'ACTIVE'")
    params: list[Any] = [strategy_id, asset, signal_type]
    if timeframe:
        sql += " AND timeframe = ?"
        params.append(timeframe)
    try:
        with get_connection() as conn:
            n = conn.execute(sql, tuple(params)).fetchone()["n"]
        return n > 0
    except sqlite3.Error as exc:
        print(f"❌ Duplicate check failed: {exc}")
        return False


def get_active_signals() -> list[Signal]:
    """Return all signals still marked ACTIVE."""
    rows = _query(
        "SELECT * FROM signals WHERE signal_status = 'ACTIVE' "
        "ORDER BY date_generated DESC"
    )
    return [Signal.from_dict(r) for r in rows]


def get_signals_today() -> list[Signal]:
    """Return all signals generated today (UTC)."""
    today = utc_now_str()[:10]
    rows = _query(
        "SELECT * FROM signals WHERE substr(date_generated, 1, 10) = ? "
        "ORDER BY date_generated DESC", (today,)
    )
    return [Signal.from_dict(r) for r in rows]


def get_signals_for_asset(asset: str) -> list[Signal]:
    """Return all signals for an asset (matches 'BTC' or 'BTC/USDT')."""
    asset = asset.upper()
    rows = _query(
        "SELECT * FROM signals WHERE upper(asset) = ? OR upper(asset) LIKE ? "
        "ORDER BY date_generated DESC", (asset, f"{asset}/%")
    )
    return [Signal.from_dict(r) for r in rows]


def get_history(page: int = 1, per_page: int = 50) -> list[Signal]:
    """Return signals newest-first, paginated."""
    page = max(1, page)
    offset = (page - 1) * per_page
    rows = _query(
        "SELECT * FROM signals ORDER BY date_generated DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    )
    return [Signal.from_dict(r) for r in rows]


def get_recent(limit: int = 20) -> list[Signal]:
    """Return the most recent `limit` signals."""
    rows = _query(
        "SELECT * FROM signals ORDER BY date_generated DESC LIMIT ?", (limit,)
    )
    return [Signal.from_dict(r) for r in rows]


# --- Lifecycle -----------------------------------------------------------

def expire_old_signals() -> int:
    """Mark ACTIVE signals past their expiry as EXPIRED. Returns count."""
    now = utc_now_str()
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "UPDATE signals SET signal_status = 'EXPIRED' "
                "WHERE signal_status = 'ACTIVE' AND date_expires <= ?", (now,)
            )
            conn.commit()
            n = cur.rowcount
        if n:
            print(f"⌛ Expired {n} old signal(s).")
        return n
    except sqlite3.Error as exc:
        print(f"❌ Failed to expire signals: {exc}")
        return 0


# --- Daily summary -------------------------------------------------------

def get_daily_summary() -> dict[str, Any]:
    """Return today's signal stats (used by API + Telegram daily summary)."""
    from storage import strategy_store

    signals = get_signals_today()
    total = len(signals)
    buys = [s for s in signals if s.signal_type == "BUY"]
    sells = [s for s in signals if s.signal_type == "SELL"]

    # Top asset / strategy by signal count today.
    def _top(items: list[str]) -> Optional[str]:
        if not items:
            return None
        return max(set(items), key=items.count)

    assets = [s.asset.split("/")[0] for s in signals]
    strat_names = [s.strategy_name for s in signals]
    avg_conf = round(sum(s.confidence_score for s in signals) / total) if total else 0

    config = load_config()
    coins = config.get("default_assets", [])
    active_strategies = sum(
        1 for c in strategy_store.list_cards()
        if isinstance(c.backtest_result, dict)
        and c.backtest_result.get("verdict") == "PASS"
    )

    return {
        "total": total,
        "buy_signals": len(buys),
        "sell_signals": len(sells),
        "top_asset": _top(assets),
        "top_strategy": _top(strat_names),
        "avg_confidence": avg_conf,
        "active_strategies": active_strategies,
        "coins_watched": coins,
    }


# --- Outcome tracking ----------------------------------------------------

def update_signal_outcomes() -> int:
    """Score maturing signals. Returns count updated.

    Signals with mechanical exits (target_price + stop_price, i.e. SMC) are
    scored EXACTLY like the backtest: walk the candles since the signal and mark
    WIN if the target is hit first, LOSS if the stop is hit first (stop checked
    first = conservative), else close at the last price once the tracking window
    (`outcome_max_track_hours`, default 24h) elapses. Signals without mechanical
    exits fall back to the legacy fixed-% threshold method.
    """
    from backtesting import data_fetcher

    config = load_config()
    win_th = float(config.get("outcome_win_threshold_pct", 1.0))
    loss_th = float(config.get("outcome_loss_threshold_pct", -1.0))
    max_track_h = float(config.get("outcome_max_track_hours", 24))

    # Pending = no final result yet (mechanical) OR a snapshot slot unfilled.
    rows = _query(
        "SELECT * FROM signals WHERE outcome_result IS NULL OR outcome_1h IS NULL "
        "OR outcome_4h IS NULL OR outcome_24h IS NULL"
    )
    if not rows:
        return 0

    now = utc_now()
    price_cache: dict[str, Optional[float]] = {}
    candle_cache: dict[tuple, Any] = {}
    updated = 0

    for data in rows:
        sig = Signal.from_dict(data)
        gen = parse_utc(sig.date_generated)
        entry = sig.entry_price_at_signal
        if gen is None or not entry:
            continue
        age_h = (now - gen).total_seconds() / 3600.0
        if age_h < 1:  # nothing matures before the 1H mark
            continue

        # --- Mechanical exits: score by which level is hit first ---------
        has_levels = (sig.target_price and sig.stop_price
                      and sig.stop_price < entry < sig.target_price)
        if has_levels and sig.outcome_result is None:
            key = (sig.asset, sig.timeframe)
            if key not in candle_cache:
                candle_cache[key] = data_fetcher.fetch_latest_ohlcv(
                    sig.asset, sig.timeframe, limit=300)
            df = candle_cache[key]
            result, exit_price = _resolve_by_levels(
                df, gen, entry, sig.target_price, sig.stop_price,
                timed_out=age_h >= max_track_h)
            if result is None:
                continue  # neither level hit yet, still inside the window
            pct = (exit_price - entry) / entry * 100.0
            sig.outcome_1h = sig.outcome_4h = sig.outcome_24h = round(pct, 3)
            sig.outcome_pct_move = round(pct, 3)
            sig.outcome_result = result
            save_signal(sig)
            updated += 1
            continue

        # --- Legacy % fallback (signals without mechanical exits) --------
        if has_levels:  # already resolved above; skip
            continue
        if sig.asset not in price_cache:
            price_cache[sig.asset] = data_fetcher.get_current_price(sig.asset)
        price = price_cache[sig.asset]
        if price is None:
            continue
        pct_move = (price - entry) / entry * 100.0
        changed = False
        if age_h >= 1 and sig.outcome_1h is None:
            sig.outcome_1h = round(pct_move, 3); changed = True
        if age_h >= 4 and sig.outcome_4h is None:
            sig.outcome_4h = round(pct_move, 3); changed = True
        if age_h >= 24 and sig.outcome_24h is None:
            sig.outcome_24h = round(pct_move, 3); changed = True
        if changed:
            sig.outcome_pct_move = round(pct_move, 3)
            sig.outcome_result = _classify(sig.signal_type, pct_move,
                                           win_th, loss_th)
            save_signal(sig)
            updated += 1

    if updated:
        print(f"📊 Updated outcomes for {updated} signals")
    return updated


def _resolve_by_levels(df, gen, entry: float, target: float, stop: float,
                       timed_out: bool):
    """Score a LONG by target/stop on candles after `gen` (backtest parity).

    Returns (result, exit_price): ('WIN', target) / ('LOSS', stop) for the first
    level hit (stop checked first within a candle = conservative). If neither is
    hit and the window has elapsed, closes at the last candle ('WIN' if above
    entry else 'LOSS'). Returns (None, None) while still pending.
    """
    if df is None or len(df) == 0:
        return (None, None)
    try:
        # Candle timestamps are tz-NAIVE UTC (pd.to_datetime(unit='ms')), while
        # parse_utc returns an AWARE datetime — comparing them raises TypeError
        # (which used to leave every signal pending forever). Strip tzinfo.
        gen_naive = gen.replace(tzinfo=None) if gen.tzinfo else gen
        after = df[df["timestamp"] > gen_naive]
    except Exception as exc:
        print(f"⚠️  [Outcomes] candle comparison failed: {exc}")
        return (None, None)
    if len(after) == 0:
        return (None, None)
    for _, c in after.iterrows():
        if float(c["low"]) <= stop:
            return ("LOSS", stop)
        if float(c["high"]) >= target:
            return ("WIN", target)
    if timed_out:
        last_close = float(after.iloc[-1]["close"])
        return ("WIN" if last_close >= entry else "LOSS", last_close)
    return (None, None)


def _classify(signal_type: str, pct_move: float,
              win_th: float, loss_th: float) -> str:
    """Classify an outcome as WIN/LOSS/NEUTRAL based on direction + move."""
    if signal_type == "BUY":
        if pct_move > win_th:
            return "WIN"
        if pct_move < loss_th:
            return "LOSS"
        return "NEUTRAL"
    # SELL: a downward move is a win.
    if pct_move < loss_th:
        return "WIN"
    if pct_move > win_th:
        return "LOSS"
    return "NEUTRAL"
