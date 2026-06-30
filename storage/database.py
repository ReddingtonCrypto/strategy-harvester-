"""
SQLite database layer for StrategyHarvester.

Owns the raw connection and schema, plus small CRUD helpers. Higher-level
code (strategy_store.py, the API) should use these functions rather than
opening their own connections.

Tables
------
strategies : every Strategy Card (list/dict fields stored as JSON text).
sources    : tracked channels/accounts to monitor later.
signals    : reserved for Phase 5 (created empty now).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from utils.helpers import utc_now_str

# Database lives in the project root next to main.py.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "strategy_harvester.db"
BACKUP_DIR = PROJECT_ROOT / "backups"
_KEEP_BACKUPS = 10


def backup_db() -> Optional[str]:
    """Snapshot the DB to backups/ (timestamped) and keep only the last 10.

    Runs at startup and before any migration/destructive op. Only a HEALTHY
    database is backed up (quick_check must pass), so corrupt files never push
    good snapshots out of the rotation. Returns the backup path, or None.
    """
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return None

    # Verify integrity before trusting this file as a backup.
    try:
        con = sqlite3.connect(DB_PATH)
        status = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
        if status != "ok":
            print(f"⚠️  [Backup] DB failed quick_check ({status}) — NOT backed "
                  f"up (existing good backups preserved).")
            return None
    except sqlite3.Error as exc:
        print(f"⚠️  [Backup] DB unreadable ({exc}) — not backed up.")
        return None

    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = BACKUP_DIR / f"strategy_harvester_{stamp}.db"
        shutil.copy2(DB_PATH, dest)
    except OSError as exc:
        print(f"⚠️  [Backup] Failed to write backup: {exc}")
        return None

    _prune_backups()
    print(f"🛟 [Backup] {dest.name} (keeping last {_KEEP_BACKUPS}).")
    return str(dest)


def _prune_backups() -> None:
    """Delete all but the newest `_KEEP_BACKUPS` snapshots."""
    try:
        snaps = sorted(BACKUP_DIR.glob("strategy_harvester_*.db"), reverse=True)
        for old in snaps[_KEEP_BACKUPS:]:
            old.unlink(missing_ok=True)
    except OSError as exc:
        print(f"⚠️  [Backup] Prune failed: {exc}")

# Fields that are stored as JSON text because SQLite has no native list type.
_JSON_FIELDS = {"indicators", "entry_conditions", "exit_conditions", "assets",
                "backtest_result", "adaptation_history"}

# Boolean fields stored as 0/1 integers.
_BOOL_FIELDS = {"approved", "is_adapted", "uses_deviation_filter"}


def get_connection() -> sqlite3.Connection:
    """Open a SQLite connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create all tables if they do not already exist.

    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    # PRIORITY ONE: snapshot the DB before ANY schema work / migration runs.
    backup_db()
    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # --- Strategy Cards -----------------------------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    id               TEXT PRIMARY KEY,
                    name             TEXT,
                    source_type      TEXT,
                    source_url       TEXT,
                    raw_content      TEXT,
                    indicators       TEXT,   -- JSON list
                    entry_conditions TEXT,   -- JSON list
                    exit_conditions  TEXT,   -- JSON list
                    timeframe        TEXT,
                    assets           TEXT,   -- JSON list
                    market_condition TEXT,
                    confidence_score INTEGER,
                    status           TEXT,
                    date_added       TEXT,
                    backtest_result  TEXT,   -- JSON or NULL
                    approved         INTEGER, -- 0/1 boolean
                    entry_rule       TEXT,   -- Phase 2: parsed entry expression
                    exit_rule        TEXT,   -- Phase 2: parsed exit expression
                    rule_notes       TEXT,   -- Phase 2: parser assumptions
                    version          INTEGER, -- Phase 3: version number
                    parent_id        TEXT,   -- Phase 3: parent version id
                    version_notes    TEXT,   -- Phase 3: what changed
                    is_adapted       INTEGER, -- Phase 3: 0/1 AI-adapted flag
                    adaptation_history TEXT,  -- Phase 3: JSON list
                    engine           TEXT,   -- Phase 6: 'generic' | 'smc'
                    engine_signal    TEXT,   -- Phase 6: range/crt/textbook/filter
                    uses_deviation_filter INTEGER -- Phase 6: 0/1
                )
                """
            )

            # --- Tracked sources ----------------------------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT,
                    identifier  TEXT,        -- channel handle / url
                    label       TEXT,
                    active      INTEGER DEFAULT 1,
                    date_added  TEXT,
                    UNIQUE(source_type, identifier)
                )
                """
            )

            # --- Signals (Phase 3: live signal engine) ------------------
            # The Phase 1 placeholder used an INTEGER id; drop it if present so
            # the full schema below (TEXT id) takes effect.
            legacy = {row["name"] for row in
                      cur.execute("PRAGMA table_info(signals)")}
            if legacy and "signal_status" not in legacy:
                cur.execute("DROP TABLE signals")
                print("🔧 Migrated: rebuilt 'signals' table for Phase 3 schema.")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id                    TEXT PRIMARY KEY,
                    strategy_id           TEXT,
                    strategy_name         TEXT,
                    asset                 TEXT,
                    timeframe             TEXT,
                    signal_type           TEXT,
                    entry_zone_low        REAL,
                    entry_zone_high       REAL,
                    current_price         REAL,
                    entry_price_at_signal REAL,
                    confidence_score      INTEGER,
                    market_trend          TEXT,
                    trend_strength        TEXT,
                    volume_confirmation   INTEGER,
                    confluence_count      INTEGER,
                    confluence_strategies TEXT,   -- JSON list
                    source                TEXT,
                    timeframe_alignment   INTEGER,
                    signal_status         TEXT,
                    date_generated        TEXT,
                    date_expires          TEXT,
                    alerted               INTEGER,
                    alert_sent_at         TEXT,
                    outcome_1h            REAL,
                    outcome_4h            REAL,
                    outcome_24h           REAL,
                    outcome_result        TEXT,
                    outcome_pct_move      REAL
                )
                """
            )

            # --- Learning insights (Phase 4) ----------------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_insights (
                    id                       TEXT PRIMARY KEY,
                    strategy_id              TEXT,
                    strategy_name            TEXT,
                    analysis_date            TEXT,
                    total_signals_analyzed   INTEGER,
                    overall_win_rate         REAL,
                    best_performing_asset    TEXT,
                    best_performing_timeframe TEXT,
                    best_market_condition    TEXT,
                    worst_market_condition   TEXT,
                    avg_confidence_winners   REAL,
                    avg_confidence_losers    REAL,
                    volume_confirmed_win_rate REAL,
                    no_volume_win_rate       REAL,
                    strong_trend_win_rate    REAL,
                    weak_trend_win_rate      REAL,
                    pattern_summary          TEXT,
                    raw_analysis             TEXT,
                    full_breakdown           TEXT,   -- JSON of all breakdowns
                    created_at               TEXT
                )
                """
            )

            # --- Adaptation suggestions (Phase 4) -----------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS adaptation_suggestions (
                    id                  TEXT PRIMARY KEY,
                    parent_strategy_id  TEXT,
                    parent_strategy_name TEXT,
                    suggestion_date     TEXT,
                    suggested_changes   TEXT,   -- JSON
                    reasoning           TEXT,
                    projected_win_rate  REAL,
                    current_win_rate    REAL,
                    improvement_pct     REAL,
                    status              TEXT,   -- PENDING / APPROVED / REJECTED
                    telegram_message_id TEXT,
                    reviewed_at         TEXT,
                    new_strategy_id     TEXT,
                    created_at          TEXT
                )
                """
            )

            # --- Sentiment checks (Phase 5) -----------------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sentiment_checks (
                    id                TEXT PRIMARY KEY,
                    asset             TEXT,
                    signal_id         TEXT,
                    overall_sentiment TEXT,
                    sentiment_score   INTEGER,
                    bullish_count     INTEGER,
                    bearish_count     INTEGER,
                    neutral_count     INTEGER,
                    per_account       TEXT,   -- JSON list
                    confidence        INTEGER,
                    summary           TEXT,
                    checked_at        TEXT
                )
                """
            )

            # --- Strategy performance (Phase 5.5) -----------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_performance (
                    strategy_id               TEXT PRIMARY KEY,
                    strategy_name             TEXT,
                    version                   INTEGER,
                    total_signals             INTEGER,
                    total_outcomes            INTEGER,
                    win_rate_overall          REAL,
                    win_rate_btc              REAL,
                    win_rate_eth              REAL,
                    win_rate_sol              REAL,
                    win_rate_bnb              REAL,
                    win_rate_1h               REAL,
                    win_rate_4h               REAL,
                    win_rate_1d               REAL,
                    win_rate_uptrend          REAL,
                    win_rate_downtrend        REAL,
                    win_rate_sideways         REAL,
                    win_rate_volume_confirmed REAL,
                    win_rate_no_volume        REAL,
                    win_rate_strong_trend     REAL,
                    win_rate_weak_trend       REAL,
                    avg_confidence_winners    REAL,
                    avg_confidence_losers     REAL,
                    best_asset                TEXT,
                    best_timeframe            TEXT,
                    best_market_condition     TEXT,
                    avoid_condition           TEXT,
                    trust_score               INTEGER,
                    last_updated              TEXT
                )
                """
            )

            # --- Parameter optimizations (Phase 5.5) --------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS parameter_optimizations (
                    id                 TEXT PRIMARY KEY,
                    strategy_id        TEXT,
                    asset              TEXT,
                    timeframe          TEXT,
                    original_params    TEXT,   -- JSON
                    optimal_params     TEXT,   -- JSON
                    original_win_rate  REAL,
                    optimal_win_rate   REAL,
                    improvement_pct    REAL,
                    tested_combinations INTEGER,
                    date_optimized     TEXT,
                    applied            INTEGER
                )
                """
            )

            # --- Regime history (Phase 5.5) -----------------------------
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS regime_history (
                    id          TEXT PRIMARY KEY,
                    asset       TEXT,
                    timeframe   TEXT,
                    regime      TEXT,
                    confidence  INTEGER,
                    recorded_at TEXT
                )
                """
            )

            # --- Scan runs (monitoring) ---------------------------------
            # One row per scan cycle (cron run). Powers the heartbeat counts,
            # daily digest, and the GitHub Pages dashboard.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanned_at    TEXT,     -- UTC ISO timestamp
                    coins_scanned INTEGER,
                    signals_found INTEGER,
                    alerts_sent   INTEGER,
                    data_source   TEXT,     -- e.g. 'bybit' / 'okx'
                    status        TEXT      -- 'ok' | 'error'
                )
                """
            )

            # --- App state (monitoring) ---------------------------------
            # Tiny key/value store for cross-run markers (e.g. last digest date)
            # that must survive the stateless GitHub Actions runs.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

            # Migrate older databases that predate later columns.
            _ensure_columns(cur, "strategies", {
                "entry_rule": "TEXT",
                "exit_rule": "TEXT",
                "rule_notes": "TEXT",
                "version": "INTEGER",
                "parent_id": "TEXT",
                "version_notes": "TEXT",
                "is_adapted": "INTEGER",
                "adaptation_history": "TEXT",
                "engine": "TEXT",
                "engine_signal": "TEXT",
                "uses_deviation_filter": "INTEGER",
            })
            # Shadow mode (live vs logged-only) — backfill existing signals.
            _ensure_columns(cur, "signals", {"mode": "TEXT"})
            cur.execute("UPDATE signals SET mode='live' WHERE mode IS NULL")
            # Mechanical exits for backtest-parity outcome scoring.
            _ensure_columns(cur, "signals", {
                "target_price": "REAL", "stop_price": "REAL"})
            conn.commit()
        print(f"✅ Database ready at {DB_PATH}")
    except sqlite3.Error as exc:
        print(f"❌ Failed to initialise database: {exc}")
        raise


def _ensure_columns(cur: sqlite3.Cursor, table: str,
                    columns: dict[str, str]) -> None:
    """Add any missing columns to an existing table (simple migration).

    `columns` maps column name -> SQL type. Existing columns are left alone.
    """
    existing = {row["name"] for row in cur.execute(f"PRAGMA table_info({table})")}
    for name, sql_type in columns.items():
        if name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
            print(f"🔧 Migrated: added '{name}' column to '{table}'.")


# --- Row <-> dict serialisation -----------------------------------------

def _encode_row(card: dict[str, Any]) -> dict[str, Any]:
    """Convert a Strategy Card dict into DB-storable primitives."""
    row = dict(card)
    for field in _JSON_FIELDS:
        if field in row:
            row[field] = json.dumps(row[field])
    for field in _BOOL_FIELDS:
        if field in row:
            row[field] = 1 if row[field] else 0
    return row


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a DB row back into a Strategy Card dict."""
    data = dict(row)
    for field in _JSON_FIELDS:
        if field in data and data[field] is not None:
            try:
                data[field] = json.loads(data[field])
            except (json.JSONDecodeError, TypeError):
                data[field] = [] if field != "backtest_result" else None
    for field in _BOOL_FIELDS:
        if field in data and data[field] is not None:
            data[field] = bool(data[field])
    return data


# --- Strategy CRUD -------------------------------------------------------

def save(card: dict[str, Any]) -> None:
    """Insert or replace a Strategy Card (keyed by id)."""
    row = _encode_row(card)
    columns = [
        "id", "name", "source_type", "source_url", "raw_content",
        "indicators", "entry_conditions", "exit_conditions", "timeframe",
        "assets", "market_condition", "confidence_score", "status",
        "date_added", "backtest_result", "approved",
        "entry_rule", "exit_rule", "rule_notes",
        "version", "parent_id", "version_notes", "is_adapted",
        "adaptation_history",
        "engine", "engine_signal", "uses_deviation_filter",
    ]
    placeholders = ", ".join("?" for _ in columns)
    values = [row.get(col) for col in columns]
    try:
        with get_connection() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO strategies "
                f"({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(f"❌ Failed to save strategy '{card.get('id')}': {exc}")
        raise


def get(strategy_id: str) -> Optional[dict[str, Any]]:
    """Return one Strategy Card dict by id, or None if not found."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
        return _decode_row(row) if row else None
    except sqlite3.Error as exc:
        print(f"❌ Failed to read strategy '{strategy_id}': {exc}")
        return None


def list_all() -> list[dict[str, Any]]:
    """Return all Strategy Cards, newest first."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM strategies ORDER BY date_added DESC, id DESC"
            ).fetchall()
        return [_decode_row(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"❌ Failed to list strategies: {exc}")
        return []


def update(strategy_id: str, fields: dict[str, Any]) -> bool:
    """Update specific fields of a Strategy Card.

    Returns True if a row was updated, False otherwise.
    """
    if not fields:
        return False
    encoded = _encode_row(fields)
    # Only update keys that are real columns.
    valid = {k: v for k, v in encoded.items() if k != "id"}
    if not valid:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in valid)
    values = list(valid.values()) + [strategy_id]
    try:
        with get_connection() as conn:
            cur = conn.execute(
                f"UPDATE strategies SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cur.rowcount > 0
    except sqlite3.Error as exc:
        print(f"❌ Failed to update strategy '{strategy_id}': {exc}")
        return False


def delete(strategy_id: str) -> bool:
    """Delete a Strategy Card by id. Returns True if a row was removed."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM strategies WHERE id = ?", (strategy_id,)
            )
            conn.commit()
            return cur.rowcount > 0
    except sqlite3.Error as exc:
        print(f"❌ Failed to delete strategy '{strategy_id}': {exc}")
        return False


# --- Source tracking -----------------------------------------------------

def add_source(source_type: str, identifier: str, label: str = "",
               date_added: str = "") -> None:
    """Register a tracked source (channel/account). Ignores duplicates."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sources "
                "(source_type, identifier, label, date_added) VALUES (?, ?, ?, ?)",
                (source_type, identifier, label, date_added),
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(f"❌ Failed to add source '{identifier}': {exc}")


def list_sources() -> list[dict[str, Any]]:
    """Return all tracked sources."""
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"❌ Failed to list sources: {exc}")
        return []


# --- Learning insights (Phase 4) ----------------------------------------

_INSIGHT_JSON = {"full_breakdown"}
_SUGGESTION_JSON = {"suggested_changes"}


def _generic_save(table: str, columns: list[str], data: dict[str, Any],
                  json_fields: set[str]) -> None:
    """Insert-or-replace a row into `table` (encoding JSON fields)."""
    row = dict(data)
    for field in json_fields:
        if field in row:
            row[field] = json.dumps(row[field])
    placeholders = ", ".join("?" for _ in columns)
    values = [row.get(c) for c in columns]
    try:
        with get_connection() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
                f"VALUES ({placeholders})", values,
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(f"❌ Failed to save into {table}: {exc}")


def _generic_decode(row: sqlite3.Row, json_fields: set[str]) -> dict[str, Any]:
    """Decode a row, parsing JSON fields back into objects."""
    data = dict(row)
    for field in json_fields:
        if data.get(field) is not None:
            try:
                data[field] = json.loads(data[field])
            except (json.JSONDecodeError, TypeError):
                data[field] = None
    return data


_INSIGHT_COLUMNS = [
    "id", "strategy_id", "strategy_name", "analysis_date",
    "total_signals_analyzed", "overall_win_rate", "best_performing_asset",
    "best_performing_timeframe", "best_market_condition",
    "worst_market_condition", "avg_confidence_winners", "avg_confidence_losers",
    "volume_confirmed_win_rate", "no_volume_win_rate", "strong_trend_win_rate",
    "weak_trend_win_rate", "pattern_summary", "raw_analysis", "full_breakdown",
    "created_at",
]


def save_insight(insight: dict[str, Any]) -> None:
    """Persist a learning_insights row."""
    _generic_save("learning_insights", _INSIGHT_COLUMNS, insight, _INSIGHT_JSON)


def get_latest_insight(strategy_id: str) -> Optional[dict[str, Any]]:
    """Return the most recent learning insight for a strategy, or None."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM learning_insights WHERE strategy_id = ? "
                "ORDER BY created_at DESC LIMIT 1", (strategy_id,)
            ).fetchone()
        return _generic_decode(row, _INSIGHT_JSON) if row else None
    except sqlite3.Error as exc:
        print(f"❌ Failed to read insight for '{strategy_id}': {exc}")
        return None


# --- Adaptation suggestions (Phase 4) -----------------------------------

_SUGGESTION_COLUMNS = [
    "id", "parent_strategy_id", "parent_strategy_name", "suggestion_date",
    "suggested_changes", "reasoning", "projected_win_rate", "current_win_rate",
    "improvement_pct", "status", "telegram_message_id", "reviewed_at",
    "new_strategy_id", "created_at",
]


def save_suggestion(suggestion: dict[str, Any]) -> None:
    """Persist an adaptation_suggestions row."""
    _generic_save("adaptation_suggestions", _SUGGESTION_COLUMNS,
                  suggestion, _SUGGESTION_JSON)


def get_suggestion(suggestion_id: str) -> Optional[dict[str, Any]]:
    """Return one adaptation suggestion by id, or None."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM adaptation_suggestions WHERE id = ?",
                (suggestion_id,)
            ).fetchone()
        return _generic_decode(row, _SUGGESTION_JSON) if row else None
    except sqlite3.Error as exc:
        print(f"❌ Failed to read suggestion '{suggestion_id}': {exc}")
        return None


def update_suggestion(suggestion_id: str, fields: dict[str, Any]) -> bool:
    """Update fields on an adaptation suggestion. Returns True on success."""
    if not fields:
        return False
    row = dict(fields)
    for field in _SUGGESTION_JSON:
        if field in row:
            row[field] = json.dumps(row[field])
    set_clause = ", ".join(f"{k} = ?" for k in row)
    values = list(row.values()) + [suggestion_id]
    try:
        with get_connection() as conn:
            cur = conn.execute(
                f"UPDATE adaptation_suggestions SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
            return cur.rowcount > 0
    except sqlite3.Error as exc:
        print(f"❌ Failed to update suggestion '{suggestion_id}': {exc}")
        return False


def list_suggestions(status: Optional[str] = None) -> list[dict[str, Any]]:
    """Return adaptation suggestions, optionally filtered by status."""
    try:
        with get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM adaptation_suggestions WHERE status = ? "
                    "ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM adaptation_suggestions "
                    "ORDER BY created_at DESC"
                ).fetchall()
        return [_generic_decode(r, _SUGGESTION_JSON) for r in rows]
    except sqlite3.Error as exc:
        print(f"❌ Failed to list suggestions: {exc}")
        return []


# --- Strategy performance (Phase 5.5) -----------------------------------

_PERFORMANCE_COLUMNS = [
    "strategy_id", "strategy_name", "version", "total_signals",
    "total_outcomes", "win_rate_overall", "win_rate_btc", "win_rate_eth",
    "win_rate_sol", "win_rate_bnb", "win_rate_1h", "win_rate_4h", "win_rate_1d",
    "win_rate_uptrend", "win_rate_downtrend", "win_rate_sideways",
    "win_rate_volume_confirmed", "win_rate_no_volume", "win_rate_strong_trend",
    "win_rate_weak_trend", "avg_confidence_winners", "avg_confidence_losers",
    "best_asset", "best_timeframe", "best_market_condition", "avoid_condition",
    "trust_score", "last_updated",
]


def save_performance(perf: dict[str, Any]) -> None:
    """Insert or replace a strategy_performance row (keyed by strategy_id)."""
    _generic_save("strategy_performance", _PERFORMANCE_COLUMNS, perf, set())


def get_performance(strategy_id: str) -> Optional[dict[str, Any]]:
    """Return performance stats for a strategy, or None."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_performance WHERE strategy_id = ?",
                (strategy_id,)
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        print(f"❌ Failed to read performance '{strategy_id}': {exc}")
        return None


def list_performance() -> list[dict[str, Any]]:
    """Return all performance rows, highest trust score first."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM strategy_performance "
                "ORDER BY trust_score DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"❌ Failed to list performance: {exc}")
        return []


# --- Parameter optimizations (Phase 5.5) --------------------------------

_OPTIMIZATION_COLUMNS = [
    "id", "strategy_id", "asset", "timeframe", "original_params",
    "optimal_params", "original_win_rate", "optimal_win_rate",
    "improvement_pct", "tested_combinations", "date_optimized", "applied",
]
_OPTIMIZATION_JSON = {"original_params", "optimal_params"}


def save_optimization(opt: dict[str, Any]) -> None:
    """Insert or replace a parameter_optimizations row."""
    row = dict(opt)
    if "applied" in row:
        row["applied"] = 1 if row["applied"] else 0
    _generic_save("parameter_optimizations", _OPTIMIZATION_COLUMNS, row,
                  _OPTIMIZATION_JSON)


def get_latest_optimization(strategy_id: str) -> Optional[dict[str, Any]]:
    """Return the most recent optimization for a strategy, or None."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM parameter_optimizations WHERE strategy_id = ? "
                "ORDER BY date_optimized DESC LIMIT 1", (strategy_id,)
            ).fetchone()
        if not row:
            return None
        data = _generic_decode(row, _OPTIMIZATION_JSON)
        data["applied"] = bool(data.get("applied"))
        return data
    except sqlite3.Error as exc:
        print(f"❌ Failed to read optimization '{strategy_id}': {exc}")
        return None


# --- Regime history (Phase 5.5) -----------------------------------------

def save_regime(regime_row: dict[str, Any]) -> None:
    """Insert a regime_history row."""
    cols = ["id", "asset", "timeframe", "regime", "confidence", "recorded_at"]
    _generic_save("regime_history", cols, regime_row, set())


def get_regime_history(asset: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent regime records for an asset, newest first."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM regime_history WHERE upper(asset) LIKE ? "
                "ORDER BY recorded_at DESC LIMIT ?",
                (f"{asset.upper()}%", limit)
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"❌ Failed to read regime history: {exc}")
        return []


# --- Scan runs (monitoring) ---------------------------------------------

def record_scan_run(scanned_at: str, coins_scanned: int, signals_found: int,
                    alerts_sent: int, data_source: str, status: str) -> None:
    """Insert one scan-cycle record (called once per cron run)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO scan_runs (scanned_at, coins_scanned, "
                "signals_found, alerts_sent, data_source, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (scanned_at, int(coins_scanned), int(signals_found),
                 int(alerts_sent), data_source, status))
            conn.commit()
    except sqlite3.Error as exc:
        print(f"⚠️  Failed to record scan run: {exc}")


def get_scan_run_stats() -> dict[str, Any]:
    """Aggregate scan-run counts (all-time + today) and the last run."""
    today = utc_now_str()[:10]
    out = {
        "scans_total": 0, "scans_today": 0,
        "signals_total": 0, "signals_today": 0,
        "last_scan_at": None, "last_status": None, "last_source": None,
    }
    try:
        with get_connection() as conn:
            r = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(signals_found),0) s "
                "FROM scan_runs").fetchone()
            out["scans_total"], out["signals_total"] = r["c"], r["s"]
            r = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(signals_found),0) s "
                "FROM scan_runs WHERE substr(scanned_at,1,10)=?",
                (today,)).fetchone()
            out["scans_today"], out["signals_today"] = r["c"], r["s"]
            last = conn.execute(
                "SELECT scanned_at, status, data_source FROM scan_runs "
                "ORDER BY id DESC LIMIT 1").fetchone()
            if last:
                out["last_scan_at"] = last["scanned_at"]
                out["last_status"] = last["status"]
                out["last_source"] = last["data_source"]
    except sqlite3.Error as exc:
        print(f"⚠️  Failed to read scan stats: {exc}")
    return out


def count_scans_on(date_str: str) -> int:
    """Number of scan runs recorded on a given UTC date (YYYY-MM-DD)."""
    try:
        with get_connection() as conn:
            r = conn.execute(
                "SELECT COUNT(*) c FROM scan_runs WHERE substr(scanned_at,1,10)=?",
                (date_str,)).fetchone()
            return int(r["c"])
    except sqlite3.Error:
        return 0


# --- App state (monitoring) ---------------------------------------------

def get_state(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a value from the app_state key/value store."""
    try:
        with get_connection() as conn:
            r = conn.execute(
                "SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default
    except sqlite3.Error:
        return default


def set_state(key: str, value: str) -> None:
    """Write a value to the app_state key/value store (upsert)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
            conn.commit()
    except sqlite3.Error as exc:
        print(f"⚠️  Failed to set state {key}: {exc}")


# --- Signal performance breakdown (dashboard) ---------------------------

def signal_breakdown() -> list[dict[str, Any]]:
    """Per (strategy, timeframe, mode) signal stats: count, win%, profit factor.

    Pure read from the signals table — wins/losses use outcome_result, the
    profit factor uses outcome_pct_move. Used by the monitoring dashboard to
    compare strategy+timeframe combos (live vs shadow) before promoting any.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT strategy_name, timeframe, "
                "COALESCE(mode,'live') AS mode, outcome_result, outcome_pct_move "
                "FROM signals"
            ).fetchall()
    except sqlite3.Error as exc:
        print(f"⚠️  Failed to read signal breakdown: {exc}")
        return []

    groups: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        key = (r["strategy_name"] or "?", r["timeframe"] or "?",
               r["mode"] or "live")
        g = groups.setdefault(key, {
            "strategy_name": key[0], "timeframe": key[1], "mode": key[2],
            "signals": 0, "wins": 0, "losses": 0,
            "_gross_win": 0.0, "_gross_loss": 0.0,
        })
        g["signals"] += 1
        res = (r["outcome_result"] or "").upper()
        move = abs(float(r["outcome_pct_move"] or 0.0))
        if res == "WIN":
            g["wins"] += 1
            g["_gross_win"] += move
        elif res == "LOSS":
            g["losses"] += 1
            g["_gross_loss"] += move

    out: list[dict[str, Any]] = []
    for g in groups.values():
        decided = g["wins"] + g["losses"]
        g["pending"] = g["signals"] - decided
        g["win_rate"] = round(g["wins"] / decided * 100, 1) if decided else 0.0
        if g["_gross_loss"] > 0:
            g["profit_factor"] = round(g["_gross_win"] / g["_gross_loss"], 2)
        elif g["_gross_win"] > 0:
            g["profit_factor"] = 999.99
        else:
            g["profit_factor"] = 0.0
        g.pop("_gross_win"); g.pop("_gross_loss")
        out.append(g)
    # Live first, then by signal count desc.
    out.sort(key=lambda x: (x["mode"] != "live", -x["signals"]))
    return out
