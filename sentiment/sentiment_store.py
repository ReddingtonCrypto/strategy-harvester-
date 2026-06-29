"""
Sentiment storage (Phase 5).

Persists sentiment checks to the `sentiment_checks` table and serves the most
recent (fresh) sentiment for an asset so repeated signals don't spam the X /
Claude APIs.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any, Optional

from storage.database import get_connection
from utils.helpers import generate_id, load_config, parse_utc, utc_now, utc_now_str

_COLUMNS = [
    "id", "asset", "signal_id", "overall_sentiment", "sentiment_score",
    "bullish_count", "bearish_count", "neutral_count", "per_account",
    "confidence", "summary", "checked_at",
]


def save_sentiment(asset: str, sentiment_data: dict[str, Any],
                   signal_id: Optional[str] = None) -> dict[str, Any]:
    """Persist a sentiment check for an asset. Returns the stored row dict."""
    row = {
        "id": generate_id(),
        "asset": asset.upper(),
        "signal_id": signal_id,
        "overall_sentiment": sentiment_data.get("overall_sentiment", "NEUTRAL"),
        "sentiment_score": int(sentiment_data.get("sentiment_score", 0) or 0),
        "bullish_count": int(sentiment_data.get("bullish_count", 0) or 0),
        "bearish_count": int(sentiment_data.get("bearish_count", 0) or 0),
        "neutral_count": int(sentiment_data.get("neutral_count", 0) or 0),
        "per_account": json.dumps(sentiment_data.get("per_account", []) or []),
        "confidence": int(sentiment_data.get("confidence", 0) or 0),
        "summary": sentiment_data.get("summary", ""),
        "checked_at": utc_now_str(),
    }
    placeholders = ", ".join("?" for _ in _COLUMNS)
    values = [row.get(c) for c in _COLUMNS]
    try:
        with get_connection() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO sentiment_checks "
                f"({', '.join(_COLUMNS)}) VALUES ({placeholders})", values,
            )
            conn.commit()
        print(f"💾 [Sentiment] Saved {row['overall_sentiment']} for {asset}.")
    except sqlite3.Error as exc:
        print(f"❌ [Sentiment] Failed to save: {exc}")
    return _decode(row)


def get_latest_sentiment(asset: str,
                         cache_minutes: Optional[int] = None
                         ) -> Optional[dict[str, Any]]:
    """Return the most recent sentiment for an asset, or None if stale/absent.

    "Stale" = older than `cache_minutes` (defaults to config
    `sentiment_cache_minutes`). A None return means a fresh check is needed.
    """
    if cache_minutes is None:
        cache_minutes = int(load_config().get("sentiment_cache_minutes", 60))
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sentiment_checks WHERE asset = ? "
                "ORDER BY checked_at DESC LIMIT 1", (asset.upper(),)
            ).fetchone()
    except sqlite3.Error as exc:
        print(f"❌ [Sentiment] Read failed: {exc}")
        return None
    if not row:
        return None

    data = _decode(row)
    checked = parse_utc(data.get("checked_at", ""))
    if checked is None or (utc_now() - checked) > timedelta(minutes=cache_minutes):
        return None  # stale → caller should refresh
    return data


def _decode(row) -> dict[str, Any]:
    """Decode a row/dict, parsing the per_account JSON."""
    data = dict(row)
    if isinstance(data.get("per_account"), str):
        try:
            data["per_account"] = json.loads(data["per_account"])
        except (json.JSONDecodeError, TypeError):
            data["per_account"] = []
    return data
