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
