"""
Telegram channel reader.

Uses Telethon to connect with API credentials from .env and pull the last N
messages from a public channel, combining them into one clean text block.

Telethon is async, so the public `read()` method wraps the async work in a
synchronous call for easy use from the terminal menu.

Required .env values:
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE
"""

from __future__ import annotations

import asyncio

from ingestion.base_reader import BaseReader, IngestionError
from utils.helpers import clean_text, get_env

# Telethon stores a login session here so you only authenticate once.
_SESSION_NAME = "strategy_harvester_session"


class TelegramReader(BaseReader):
    """Reader for public Telegram channels."""

    source_type = "telegram"

    def read(self, channel: str, limit: int = 50) -> str:
        """Return the last `limit` messages from a public channel as text.

        Parameters
        ----------
        channel : str
            Public channel username (with or without leading '@') or t.me link.
        limit : int
            How many recent messages to fetch (default 50).
        """
        api_id = get_env("TELEGRAM_API_ID")
        api_hash = get_env("TELEGRAM_API_HASH")
        phone = get_env("TELEGRAM_PHONE")

        if not (api_id and api_hash and phone):
            raise IngestionError(
                "Missing Telegram credentials in .env "
                "(need TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE)."
            )

        try:
            api_id_int = int(api_id)
        except ValueError as exc:
            raise IngestionError("TELEGRAM_API_ID must be a number.") from exc

        channel = self._normalise_channel(channel)
        print(f"📡 [Telegram] Connecting to fetch {limit} messages from '{channel}'...")

        try:
            return asyncio.run(
                self._fetch(api_id_int, api_hash, phone, channel, limit)
            )
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(f"Telegram read failed: {exc}") from exc

    # ----- Internals -----------------------------------------------------

    @staticmethod
    def _normalise_channel(channel: str) -> str:
        """Strip URL/`@` decorations down to a bare username."""
        channel = (channel or "").strip()
        channel = channel.replace("https://t.me/", "").replace("t.me/", "")
        channel = channel.lstrip("@")
        if not channel:
            raise IngestionError("No channel username provided.")
        return channel

    async def _fetch(self, api_id: int, api_hash: str, phone: str,
                     channel: str, limit: int) -> str:
        """Async worker: connect, pull messages, return combined text."""
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise IngestionError(
                "telethon is not installed (pip install telethon)."
            ) from exc

        client = TelegramClient(_SESSION_NAME, api_id, api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                # First run: requires interactive login code from Telegram.
                print("🔐 [Telegram] Authorising — check your Telegram app for a code.")
                await client.start(phone=phone)

            messages: list[str] = []
            async for msg in client.iter_messages(channel, limit=limit):
                if msg.text:
                    messages.append(msg.text)

            if not messages:
                raise IngestionError(f"No text messages found in '{channel}'.")

            # Oldest -> newest reads more naturally for the extractor.
            messages.reverse()
            combined = "\n\n".join(messages)
            print(f"✅ [Telegram] Fetched {len(messages)} messages.")
            return clean_text(combined)
        finally:
            await client.disconnect()


def read_telegram(channel: str, limit: int = 50) -> str:
    """Shortcut: return combined text from a public Telegram channel."""
    return TelegramReader().read(channel, limit=limit)


# --- Watchlist processing (Phase 1: autonomous content intelligence) ----
#
# Reads its channel list from the `sources` table and only fetches messages
# newer than each source's stored checkpoint (Telegram message ids are
# sequential per channel, so a plain integer comparison — via Telethon's
# `min_id` — is enough). Designed to run headlessly: unlike the interactive
# `read()` above, this NEVER calls `client.start(phone=...)`, which would
# block on input() for a login code with no terminal attached. Instead it
# only uses an already-authorized session — either the local file-based
# session created once interactively (menu option 3), or a portable
# TELEGRAM_SESSION_STRING env var for CI. If neither is authorized, or the
# three base credentials are missing, this returns a clear "skipped" result
# instead of raising, so a scheduled job can log it and move on.

def _has_credentials() -> bool:
    """True if the three base Telegram API credentials are all present."""
    return bool(get_env("TELEGRAM_API_ID") and get_env("TELEGRAM_API_HASH")
                and get_env("TELEGRAM_PHONE"))


def _build_client(api_id: int, api_hash: str):
    """Build a Telethon client, preferring a portable session for headless use.

    `TELEGRAM_SESSION_STRING` (a Telethon StringSession) is checked first —
    required for CI, since there's no local file system state to persist a
    session between runs. Falls back to the same local file-based session
    the interactive reader uses, for local/manual runs.
    """
    from telethon import TelegramClient

    session_string = get_env("TELEGRAM_SESSION_STRING")
    if session_string:
        from telethon.sessions import StringSession

        return TelegramClient(StringSession(session_string), api_id, api_hash)
    return TelegramClient(_SESSION_NAME, api_id, api_hash)


def process_watchlist(*, extraction_mode: "str | None" = None) -> dict:
    """Process every active Telegram source in the `sources` table.

    Headless-safe: never blocks on input(). Returns a summary dict —
    sources_checked, messages_processed, strategies_found, errors (list of
    "source: message" strings), and skipped_reason (set + logged instead of
    raising when credentials/session aren't available).
    """
    summary: dict = {"sources_checked": 0, "messages_processed": 0,
                     "strategies_found": 0, "errors": [], "skipped_reason": None}

    if not _has_credentials():
        msg = ("Telegram credentials missing (need TELEGRAM_API_ID, "
               "TELEGRAM_API_HASH, TELEGRAM_PHONE) — skipping Telegram "
               "watchlist processing this run.")
        print(f"⚠️  [Telegram] {msg}")
        summary["skipped_reason"] = msg
        return summary

    from storage import database as db

    sources = db.list_sources(source_type="telegram", active_only=True)
    if not sources:
        print("ℹ️  [Telegram] No active Telegram sources in the watchlist.")
        return summary

    try:
        return asyncio.run(_process_watchlist_async(sources, extraction_mode, summary))
    except IngestionError as exc:
        print(f"⚠️  [Telegram] {exc}")
        summary["skipped_reason"] = str(exc)
        return summary


async def _process_watchlist_async(sources: list[dict], extraction_mode,
                                   summary: dict) -> dict:
    """Async worker behind process_watchlist(). See that function's docstring."""
    from extraction.strategy_extractor import extract_strategy
    from storage import database as db, strategy_store
    from utils.helpers import load_config, utc_now_str

    api_id = int(get_env("TELEGRAM_API_ID"))  # validated non-empty by caller
    api_hash = get_env("TELEGRAM_API_HASH")
    fetch_limit = int(load_config().get("messages_to_fetch", 50))

    client = _build_client(api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise IngestionError(
                "Telegram session is not authorized. This function never "
                "attempts an interactive login (it would block on input() "
                "with no terminal attached). Authorize once locally — e.g. "
                "via the interactive menu (option 3) — then set "
                "TELEGRAM_SESSION_STRING for headless/CI use. "
                "See SUMMARY_PHASE1.md."
            )

        for source in sources:
            summary["sources_checked"] += 1
            identifier = source["identifier"]
            label = source.get("label") or identifier
            try:
                channel = TelegramReader._normalise_channel(identifier)
                print(f"📡 [Telegram] Checking watchlist source: {label} (@{channel})")

                checkpoint = source.get("last_item_id")
                min_id = int(checkpoint) if checkpoint else 0
                messages: list[str] = []
                newest_id = min_id
                async for msg in client.iter_messages(
                        channel, min_id=min_id, limit=fetch_limit):
                    if msg.id > newest_id:
                        newest_id = msg.id
                    if msg.text:
                        messages.append(msg.text)

                if newest_id == min_id:
                    print("   – no new messages since last check.")
                    db.update_source_checkpoint(source["id"], utc_now_str())
                    continue

                if not messages:
                    # New messages existed (ids advanced) but none had text
                    # (media-only posts) — still advance the checkpoint.
                    print("   – new messages found but none had text.")
                    db.update_source_checkpoint(
                        source["id"], utc_now_str(), str(newest_id))
                    continue

                messages.reverse()  # oldest -> newest reads more naturally
                combined = clean_text("\n\n".join(messages))
                print(f"   {len(messages)} new message(s) — extracting...")
                card = extract_strategy(
                    combined, source_type="telegram", source_url=channel,
                    force_mode=extraction_mode)
                if card and card.confidence_score > 0:
                    strategy_store.save_card(card)
                    summary["strategies_found"] += 1
                    print(f"   ✅ strategy found: {card.name}")
                summary["messages_processed"] += len(messages)

                db.update_source_checkpoint(
                    source["id"], utc_now_str(), str(newest_id))
            except Exception as exc:  # noqa: BLE001 — one bad source
                # shouldn't stop the rest of the watchlist.
                print(f"   ❌ Source failed: {exc}")
                summary["errors"].append(f"{identifier}: {exc}")
                continue
    finally:
        await client.disconnect()

    return summary


# --- Standalone single-message processing (Phase 4) ---------------------
#
# Same spirit as youtube_reader.YouTubeReader.read() for a single video:
# a one-off "extract this specific thing right now" action, independent of
# the watchlist/checkpoint system above. Takes a public message LINK
# (https://t.me/channelname/12345), not a channel name.

def _parse_message_link(url: str) -> tuple[str, int] | None:
    """Parse a public t.me channel message link into (channel, message_id).

    Returns None if the link isn't in the expected 't.me/<channel>/<id>'
    form (private/internal 't.me/c/<id>/<id>' links aren't supported).
    """
    import re

    m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", url or "")
    if not m:
        return None
    return m.group(1), int(m.group(2))


def process_single_message(message_url: str, *, extraction_mode: str | None = None
                           ) -> dict:
    """Fetch and extract ONE standalone Telegram message immediately.

    Headless-safe: never calls input() or an interactive login. Returns
    {"strategy": <name or None>, "error": <message or None>} — check
    "error" first; a None strategy with no error means the message had no
    text or no strategy was found in it (not a failure).
    """
    result: dict = {"strategy": None, "error": None}

    parsed = _parse_message_link(message_url)
    if not parsed:
        result["error"] = ("Could not parse a channel/message id from that link. "
                           "Expected format: https://t.me/channelname/12345")
        return result
    channel, message_id = parsed

    if not _has_credentials():
        result["error"] = ("Telegram credentials missing (TELEGRAM_API_ID/API_HASH/"
                           "PHONE) — cannot fetch this message.")
        return result

    try:
        return asyncio.run(
            _process_single_message_async(channel, message_id, extraction_mode))
    except IngestionError as exc:
        result["error"] = str(exc)
        return result


async def _process_single_message_async(channel: str, message_id: int,
                                        extraction_mode: str | None) -> dict:
    """Async worker behind process_single_message(). See that docstring."""
    from extraction.strategy_extractor import extract_strategy
    from storage import strategy_store

    result: dict = {"strategy": None, "error": None}
    api_id = int(get_env("TELEGRAM_API_ID"))  # validated non-empty by caller
    api_hash = get_env("TELEGRAM_API_HASH")

    client = _build_client(api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            result["error"] = (
                "Telegram session is not authorized. This never attempts an "
                "interactive login (it would block with no terminal "
                "attached) — authorize once locally (menu option 3), then "
                "set TELEGRAM_SESSION_STRING. See SUMMARY_PHASE1.md.")
            return result

        msg = await client.get_messages(channel, ids=message_id)
        if not msg or not getattr(msg, "text", None):
            result["error"] = ("That message has no text, doesn't exist, or "
                               "isn't accessible (private channel?).")
            return result

        card = extract_strategy(
            clean_text(msg.text), source_type="telegram",
            source_url=f"https://t.me/{channel}/{message_id}",
            force_mode=extraction_mode)
        if card and card.confidence_score > 0:
            strategy_store.save_card(card)
            result["strategy"] = card.name
        return result
    finally:
        await client.disconnect()
