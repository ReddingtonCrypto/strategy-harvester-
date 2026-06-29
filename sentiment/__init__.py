"""
Twitter/X sentiment engine (Phase 5).

When a signal fires, checks recent sentiment from trusted X accounts about that
coin (via Claude Opus 4.8), adjusts the signal's confidence, and adds a
sentiment section to the Telegram alert. Sentiment is an *enhancement only* —
any failure is skipped silently and never blocks the alert.

Importing helpers ensures the UTF-8 console reconfigure runs (Windows safety).
"""

from utils import helpers as _helpers  # noqa: F401
