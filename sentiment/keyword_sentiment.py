"""
Rule-based keyword sentiment engine (Phase 5.5, Part 1).

A pure-Python, zero-LLM sentiment scorer used in the LIVE scanner — instant,
free, and 24/7. The Claude-based `sentiment_analyzer` is kept only as a deep
fallback for daily analysis, never for live signals.

score range: -100 (extreme bearish) .. +100 (extreme bullish).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from sentiment import sentiment_store
from utils.helpers import load_config

# --- Weighted keyword lexicon -------------------------------------------
STRONG_BULLISH = ["moon", "mooning", "breakout", "explode", "massive buy",
                  "strong buy", "accumulate", "all time high", "ath",
                  "parabolic", "surge", "skyrocket", "bullrun", "bull run",
                  "reversal up", "bottom confirmed", "demand zone",
                  "support held"]
BULLISH = ["bullish", "buy", "long", "uptrend", "rally", "bounce", "recovery",
           "green", "pump", "up", "higher", "target", "resistance break",
           "golden cross", "oversold", "dip buy"]
MILD_BULLISH = ["positive", "good", "nice", "interesting", "watching",
                "potential", "setup", "loading"]
STRONG_BEARISH = ["crash", "dump", "collapse", "rekt", "liquidated",
                  "death cross", "breakdown", "falling", "capitulate",
                  "bear market", "downtrend confirmed", "sell off",
                  "massive sell", "distribution", "top confirmed"]
BEARISH = ["bearish", "sell", "short", "downtrend", "drop", "fall", "red",
           "bleeding", "lower", "resistance", "overbought", "correction",
           "pullback", "reject"]
MILD_BEARISH = ["careful", "caution", "risky", "warning", "unsure", "wait",
                "avoid", "concern"]

# weight, keyword-list pairs (checked longest-phrase-first within each list).
_WEIGHTED = (
    (3, STRONG_BULLISH), (2, BULLISH), (1, MILD_BULLISH),
    (-3, STRONG_BEARISH), (-2, BEARISH), (-1, MILD_BEARISH),
)

# Symbol → spoken names for "is this coin mentioned?" detection.
_COIN_NAMES = {
    "BTC": ["btc", "bitcoin"], "ETH": ["eth", "ethereum", "ether"],
    "SOL": ["sol", "solana"], "BNB": ["bnb", "binance coin", "binancecoin"],
    "XRP": ["xrp", "ripple"], "ADA": ["ada", "cardano"],
    "DOGE": ["doge", "dogecoin"],
}

_COIN_MULTIPLIER = 1.5
_RAW_TO_SCORE = 10  # scale raw keyword score → -100..100 range


def _mentions_asset(text: str, asset: str) -> bool:
    """True if the text mentions the asset (symbol or common name)."""
    asset = asset.split("/")[0].upper()
    names = _COIN_NAMES.get(asset, [asset.lower()])
    return any(re.search(rf"\b{re.escape(n)}\b", text) for n in names)


def score_post(text: str, asset: str) -> dict[str, Any]:
    """Score a single post's sentiment toward `asset` using keyword matching."""
    low = (text or "").lower()
    asset_mentioned = _mentions_asset(low, asset)

    raw = 0
    matched: list[str] = []
    for weight, words in _WEIGHTED:
        for kw in words:
            # Word-boundary match so 'up' doesn't match 'support', etc.
            if re.search(rf"\b{re.escape(kw)}\b", low):
                raw += weight
                matched.append(kw)

    if asset_mentioned:
        raw *= _COIN_MULTIPLIER

    score = max(-100, min(100, round(raw * _RAW_TO_SCORE)))
    sentiment = _classify(score)
    return {
        "score": score,
        "sentiment": sentiment,
        "matched_keywords": matched,
        "asset_mentioned": asset_mentioned,
    }


def _classify(score: float) -> str:
    """Map a numeric score to a sentiment label."""
    if score > 30:
        return "BULLISH"
    if score < -30:
        return "BEARISH"
    return "NEUTRAL"


def analyze_sentiment_fast(posts: list[dict[str, Any]], asset: str
                           ) -> dict[str, Any]:
    """Aggregate keyword scores across posts into an overall sentiment read."""
    asset_base = asset.split("/")[0].upper()
    if not posts:
        return {"overall_sentiment": "NEUTRAL", "sentiment_score": 0,
                "bullish_count": 0, "bearish_count": 0, "neutral_count": 0,
                "confidence": 0, "summary": f"No posts about {asset_base}.",
                "per_account": [], "method": "keyword_scoring"}

    scored = []
    bullish = bearish = neutral = 0
    per_account = []
    for p in posts:
        s = score_post(p.get("text", ""), asset_base)
        scored.append(s["score"])
        if s["sentiment"] == "BULLISH":
            bullish += 1
        elif s["sentiment"] == "BEARISH":
            bearish += 1
        else:
            neutral += 1
        per_account.append({
            "username": p.get("username", ""),
            "sentiment": s["sentiment"],
            "key_quote": ", ".join(s["matched_keywords"][:4]) or "no keywords",
        })

    avg = round(sum(scored) / len(scored))
    total = len(posts)

    # Overall label: thresholds, with MIXED when both camps are present.
    if avg > 30:
        overall = "BULLISH"
    elif avg < -30:
        overall = "BEARISH"
    elif bullish > 0 and bearish > 0:
        overall = "MIXED"
    else:
        overall = "NEUTRAL"

    dominant = max(bullish, bearish, neutral) / total
    confidence = min(100, round(dominant * 80 + min(total, 5) * 4))

    majority = bullish if overall == "BULLISH" else (
        bearish if overall == "BEARISH" else max(bullish, bearish))
    summary = f"{majority}/{total} accounts {overall.lower()} on {asset_base}"

    return {
        "overall_sentiment": overall,
        "sentiment_score": avg,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "confidence": confidence,
        "summary": summary,
        "per_account": per_account,
        "method": "keyword_scoring",
    }


def check_sentiment_fast(asset: str, signal_type: str,
                         signal_id: Optional[str] = None
                         ) -> Optional[dict[str, Any]]:
    """Live-scanner entry point: keyword sentiment for an asset (zero LLM).

    Uses cached results within `sentiment_cache_minutes`. Returns None (skip)
    when sentiment is disabled, no trusted accounts are set, or no posts found.
    """
    config = load_config()
    if not config.get("sentiment_enabled", True):
        return None
    accounts = config.get("trusted_x_accounts", [])
    if not accounts:
        return None

    asset_base = asset.split("/")[0].upper()
    cached = sentiment_store.get_latest_sentiment(asset_base)
    if cached:
        return cached

    posts = _gather_posts(accounts, asset_base)
    if not posts:
        return None

    result = analyze_sentiment_fast(posts, asset_base)
    return sentiment_store.save_sentiment(asset_base, result, signal_id)


def _gather_posts(accounts: list[str], asset: str) -> list[dict[str, Any]]:
    """Collect recent posts from trusted accounts (X fetch, no LLM)."""
    from ingestion import twitter_reader

    posts: list[dict[str, Any]] = []
    for acc in accounts:
        try:
            posts.extend(twitter_reader.fetch_account_posts(acc, 10))
        except Exception as exc:
            print(f"⚠️  [KeywordSentiment] @{acc} fetch failed: {exc}")
    return posts
