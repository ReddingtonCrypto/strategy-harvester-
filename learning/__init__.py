"""
Strategy learning + adaptation engine (Phase 4).

Analyzes stored signal outcomes, finds patterns (via Claude Opus 4.8), suggests
improvements, and — only on explicit user approval — creates new *versioned*
strategies. Originals are never modified. Claude is used ONLY for offline
analysis/suggestion, never during live scanning.

Importing helpers ensures the UTF-8 console reconfigure runs (Windows safety).
"""

from utils import helpers as _helpers  # noqa: F401
