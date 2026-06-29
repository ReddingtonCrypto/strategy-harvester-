"""
Shared utility functions for StrategyHarvester.

Keeps small, reusable helpers (config loading, id generation, text cleaning,
JSON parsing) in one place so every module can rely on the same behaviour.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# --- Console encoding ----------------------------------------------------
# On Windows the default console encoding is often cp1252, which cannot encode
# the emoji used in our status messages and would raise UnicodeEncodeError.
# Reconfigure stdout/stderr to UTF-8 (with replacement) so progress messages
# print safely everywhere. This runs once, on first import of this module
# (which every entry point pulls in early).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # py3.7+
    except (AttributeError, ValueError):
        pass

# --- Project paths -------------------------------------------------------
# Resolve paths relative to the project root (the folder containing main.py)
# so the app works no matter which directory it is launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

# Sensible fallback if config.json is missing or unreadable.
_DEFAULT_CONFIG: dict[str, Any] = {
    "default_assets": ["BTC", "ETH", "SOL", "BNB"],
    "default_timeframes": ["1H", "4H", "1D"],
    "manual_mode": True,
    "messages_to_fetch": 50,
    "min_confidence_score": 60,
}


def load_config() -> dict[str, Any]:
    """Load config.json from the project root.

    Returns the parsed config dict, falling back to sane defaults (and
    printing a warning) if the file is missing or invalid.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            config = json.load(fh)
        # Merge over defaults so missing keys never raise KeyError downstream.
        merged = {**_DEFAULT_CONFIG, **config}
        return merged
    except FileNotFoundError:
        print(f"⚠️  config.json not found at {CONFIG_PATH} — using defaults.")
        return dict(_DEFAULT_CONFIG)
    except json.JSONDecodeError as exc:
        print(f"⚠️  config.json is not valid JSON ({exc}) — using defaults.")
        return dict(_DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> bool:
    """Write the config dict back to config.json. Returns True on success."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
        return True
    except OSError as exc:
        print(f"❌ Failed to write config.json: {exc}")
        return False


def generate_id() -> str:
    """Return a short, unique, URL-safe id for a Strategy Card."""
    return uuid.uuid4().hex[:12]


def today_str() -> str:
    """Return today's date as an ISO 'YYYY-MM-DD' string."""
    return date.today().isoformat()


def now_iso() -> str:
    """Return the current timestamp as an ISO 8601 string."""
    return datetime.now().isoformat(timespec="seconds")


# --- UTC time helpers (Phase 3: all signal timestamps are UTC) -----------

UTC_FORMAT = "%Y-%m-%d %H:%M:%S"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_str() -> str:
    """Return the current UTC time as 'YYYY-MM-DD HH:MM:SS'."""
    return utc_now().strftime(UTC_FORMAT)


def parse_utc(text: str) -> Optional[datetime]:
    """Parse a 'YYYY-MM-DD HH:MM:SS' UTC string into an aware datetime.

    Returns None if the string is empty or malformed.
    """
    if not text:
        return None
    try:
        return datetime.strptime(text, UTC_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def clean_text(text: str) -> str:
    """Normalise raw text for downstream processing.

    - Collapses runs of whitespace/newlines.
    - Strips leading/trailing whitespace.
    - Removes obvious control characters.

    Returns an empty string if given falsy input.
    """
    if not text:
        return ""
    # Remove non-printable control chars (keep normal whitespace).
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    # Collapse 3+ newlines into a paragraph break.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of spaces/tabs.
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from an LLM response.

    Handles responses that are pure JSON, JSON wrapped in ```json fences,
    or JSON with surrounding prose. Returns the parsed dict, or None if no
    valid JSON object could be found.
    """
    if not text:
        return None

    # 1) Try the whole string first.
    candidates: list[str] = [text.strip()]

    # 2) Try content inside a fenced code block.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidates.insert(0, fence.group(1))

    # 3) Try the first {...} balanced-ish block in the text.
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def get_env(key: str, default: str | None = None) -> str | None:
    """Read an environment variable, treating empty strings as unset."""
    value = os.getenv(key, default)
    if value is not None and value.strip() == "":
        return default
    return value
