"""
Content ingestion package.

Every reader returns a plain-text string via a `.read()`-style call so that
new sources (Twitter, Instagram, RSS, ...) can be dropped in later by simply
adding a new module here that follows the same `BaseReader` contract — no
restructuring required elsewhere in the app.
"""

from .base_reader import BaseReader, IngestionError

__all__ = ["BaseReader", "IngestionError"]
