"""
Twitter/X sentiment analyzer (Phase 5, Part C).

When a signal fires, gathers recent posts about the coin from trusted X
accounts and asks Claude Opus 4.8 for an overall sentiment read, which is then
used to nudge the signal's confidence and enrich the Telegram alert.

Enhancement only: if there are no trusted accounts, the X API fails, or Claude
is unavailable, this returns None and the caller proceeds without sentiment.
"""

from __future__ import annotations

from typing import Any, Optional

from sentiment import sentiment_store
from utils.helpers import extract_json, get_env, load_config

_MODEL = "claude-opus-4-8"

SENTIMENT_PROMPT = """\
You are a crypto market sentiment analyst.
Read these recent posts from trusted crypto traders
about [ASSET].

Posts:
[POSTS]

Analyze the overall sentiment toward [ASSET].
Consider: tone, price predictions, buy/sell mentions,
bullish/bearish language, urgency.

Respond ONLY in this JSON format:
{
  "overall_sentiment": "BULLISH/BEARISH/NEUTRAL/MIXED",
  "sentiment_score": 0,
  "bullish_count": 0,
  "bearish_count": 0,
  "neutral_count": 0,
  "per_account": [
    {
      "username": "",
      "sentiment": "BULLISH/BEARISH/NEUTRAL",
      "key_quote": "brief summary of their view"
    }
  ],
  "confidence": 0,
  "summary": "one line summary of overall sentiment"
}

sentiment_score: -100 (extreme bearish) to +100 (extreme bullish)
"""


def check_sentiment(asset: str, signal_type: str,
                    signal_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Return a sentiment read for `asset`, or None to proceed without one."""
    config = load_config()
    if not config.get("sentiment_enabled", True):
        return None

    accounts = config.get("trusted_x_accounts", [])
    if not accounts:
        return None  # nothing to check — skip silently (per spec)

    asset = asset.split("/")[0].upper()

    # Reuse a fresh cached check to avoid spamming the APIs.
    cached = sentiment_store.get_latest_sentiment(asset)
    if cached:
        print(f"🐦 [Sentiment] Using cached sentiment for {asset} "
              f"({cached['overall_sentiment']}).")
        return cached

    posts = _gather_posts(accounts, asset)
    if not posts:
        print(f"🐦 [Sentiment] No recent {asset} posts from trusted accounts.")
        return None

    result = _call_claude(asset, posts)
    if result is None:
        return None

    return sentiment_store.save_sentiment(asset, result, signal_id)


def _gather_posts(accounts: list[str], asset: str) -> list[dict[str, Any]]:
    """Collect recent posts mentioning `asset` from the trusted accounts."""
    from ingestion import twitter_reader

    posts: list[dict[str, Any]] = []
    for acc in accounts:
        try:
            for p in twitter_reader.fetch_account_posts(acc, 10):
                if asset.lower() in p["text"].lower():
                    posts.append(p)
        except Exception as exc:  # never let one account break the check
            print(f"⚠️  [Sentiment] Could not read @{acc}: {exc}")
    return posts


def _call_claude(asset: str, posts: list[dict[str, Any]]
                 ) -> Optional[dict[str, Any]]:
    """Send the posts to Claude Opus 4.8 and return parsed sentiment JSON."""
    api_key = get_env("CLAUDE_API_KEY")
    if not api_key:
        print("⚠️  [Sentiment] No CLAUDE_API_KEY — skipping sentiment.")
        return None
    try:
        import anthropic
    except ImportError:
        print("⚠️  [Sentiment] 'anthropic' not installed — skipping.")
        return None

    posts_text = "\n".join(f"@{p['username']}: {p['text']}" for p in posts)
    prompt = (SENTIMENT_PROMPT
              .replace("[ASSET]", asset)
              .replace("[POSTS]", posts_text))
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_MODEL, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", None) == "text")
    except Exception as exc:
        print(f"⚠️  [Sentiment] Claude call failed: {exc}")
        return None
    return extract_json(text)


def confidence_adjustment(overall_sentiment: str, signal_type: str) -> int:
    """Return the confidence delta for a sentiment + signal direction.

    BULLISH+BUY → +10, BEARISH+BUY → -10, BULLISH+SELL → -10,
    BEARISH+SELL → +10, otherwise 0 (NEUTRAL / MIXED).
    """
    s = (overall_sentiment or "").upper()
    t = (signal_type or "").upper()
    if s == "BULLISH":
        return 10 if t == "BUY" else -10
    if s == "BEARISH":
        return -10 if t == "BUY" else 10
    return 0


def sentiment_to_confidence_adjustment(sentiment: dict[str, Any],
                                       signal_type: str,
                                       base_confidence: int) -> int:
    """Apply the sentiment delta to a base confidence (clamped 0-100)."""
    delta = confidence_adjustment(
        (sentiment or {}).get("overall_sentiment", "NEUTRAL"), signal_type)
    return max(0, min(100, base_confidence + delta))
