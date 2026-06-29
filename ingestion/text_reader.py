"""
Text content reader.

Accepts either raw pasted text or a path to a .txt file, cleans the
formatting, and returns structured plain text. Useful for manual notes,
copied articles, or any free-form source.
"""

from __future__ import annotations

from pathlib import Path

from ingestion.base_reader import BaseReader, IngestionError
from utils.helpers import clean_text


class TextReader(BaseReader):
    """Reader for pasted text or local .txt files."""

    source_type = "manual"

    def read(self, content: str = "", file_path: str = "") -> str:
        """Return cleaned text from a string or a file.

        Parameters
        ----------
        content : str
            Raw pasted text (used when no file_path is given).
        file_path : str
            Path to a .txt file to read instead.

        Exactly one of `content` or `file_path` should be provided.
        """
        if file_path:
            return self._read_file(file_path)
        if content and content.strip():
            print("📝 [Text] Using pasted text.")
            cleaned = clean_text(content)
            print(f"✅ [Text] Cleaned text ({len(cleaned)} chars).")
            return cleaned
        raise IngestionError("No text provided (need either content or file_path).")

    @staticmethod
    def _read_file(file_path: str) -> str:
        """Read and clean a local text file."""
        path = Path(file_path).expanduser()
        if not path.exists():
            raise IngestionError(f"File not found: {path}")
        if not path.is_file():
            raise IngestionError(f"Not a file: {path}")
        try:
            print(f"📂 [Text] Reading file: {path}")
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise IngestionError(f"Could not read file {path}: {exc}") from exc
        cleaned = clean_text(raw)
        print(f"✅ [Text] Cleaned text ({len(cleaned)} chars).")
        return cleaned


def read_text(content: str = "", file_path: str = "") -> str:
    """Shortcut: return cleaned text from a string or file path."""
    return TextReader().read(content=content, file_path=file_path)
