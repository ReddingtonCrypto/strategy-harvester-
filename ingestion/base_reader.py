"""
Base contract for all content readers.

Every ingestion source (YouTube, Telegram, manual text, and future Twitter /
Instagram / RSS readers) subclasses `BaseReader` and implements `read()`,
returning a single cleaned plain-text string. Because the rest of the app only
depends on this contract, new sources can be added without touching the
extractor, storage, or menu code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IngestionError(Exception):
    """Raised when a reader cannot produce usable text."""


class BaseReader(ABC):
    """Abstract base class every reader must implement.

    Attributes
    ----------
    source_type : str
        Short identifier stored on the Strategy Card ('youtube', 'telegram',
        'manual', ...). Subclasses should override this.
    """

    source_type: str = "unknown"

    @abstractmethod
    def read(self, *args, **kwargs) -> str:
        """Fetch content and return it as a cleaned plain-text string.

        Should raise `IngestionError` (not crash) when content can't be
        retrieved, so callers can handle failures gracefully.
        """
        raise NotImplementedError
