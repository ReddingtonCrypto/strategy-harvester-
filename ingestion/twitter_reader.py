"""
Twitter/X strategy scraper (Phase 5, Part B).

Scrapes a user-curated list of trusted X accounts for crypto-trading posts and
runs them through the existing strategy extractor. Account list lives in
config.json (never hardcoded). Uses tweepy; all X API failures are handled
gracefully (skip + log) so nothing crashes.
"""

from __future__ import annotations

from typing import Any, Optional

from extraction.strategy_extractor import extract_strategy
from models.strategy_card import StrategyCard
from storage import strategy_store
from utils.helpers import get_env, load_config, save_config

# Posts must contain at least one of these (case-insensitive) to be relevant.
CRYPTO_KEYWORDS = [
    "btc", "eth", "crypto", "trade", "signal", "buy", "sell", "long", "short",
    "setup", "rsi", "ma", "support", "resistance", "breakout", "strategy",
    "entry", "exit",
]


# --- X client ------------------------------------------------------------

def _get_client():
    """Build a tweepy v2 client from .env creds, or None if unavailable."""
    try:
        import tweepy
    except ImportError:
        print("⚠️  [X] tweepy not installed (pip install tweepy).")
        return None

    bearer = get_env("X_BEARER_TOKEN")
    if bearer:
        return tweepy.Client(bearer_token=bearer)

    key = get_env("X_API_KEY")
    secret = get_env("X_API_KEY_SECRET")
    token = get_env("X_ACCESS_TOKEN")
    token_secret = get_env("X_ACCESS_TOKEN_SECRET")
    if all([key, secret, token, token_secret]):
        return tweepy.Client(consumer_key=key, consumer_secret=secret,
                             access_token=token, access_token_secret=token_secret)
    print("⚠️  [X] No X API credentials in .env — X features disabled.")
    return None


def _has_keyword(text: str) -> bool:
    """True if the text mentions any crypto keyword."""
    low = (text or "").lower()
    return any(kw in low for kw in CRYPTO_KEYWORDS)


# --- Trusted account management -----------------------------------------

def list_trusted_accounts() -> list[str]:
    """Return the configured list of trusted X usernames."""
    return load_config().get("trusted_x_accounts", [])


def add_trusted_account(username: str) -> bool:
    """Validate an account exists on X and add it to config. Returns success."""
    username = username.strip().lstrip("@")
    if not username:
        print("⚠️  [X] No username given.")
        return False

    config = load_config()
    accounts = config.get("trusted_x_accounts", [])
    if username in accounts:
        print(f"ℹ️  [X] @{username} is already trusted.")
        return True

    client = _get_client()
    if client is not None:
        try:
            user = client.get_user(username=username)
            if not getattr(user, "data", None):
                print(f"❌ [X] @{username} not found on X.")
                return False
        except Exception as exc:
            print(f"⚠️  [X] Could not validate @{username} ({exc}); adding anyway.")

    accounts.append(username)
    config["trusted_x_accounts"] = accounts
    if save_config(config):
        print(f"✅ @{username} added to trusted accounts")
        return True
    return False


def remove_trusted_account(username: str) -> bool:
    """Remove an account from the trusted list. Returns success."""
    username = username.strip().lstrip("@")
    config = load_config()
    accounts = config.get("trusted_x_accounts", [])
    if username not in accounts:
        print(f"ℹ️  [X] @{username} is not in the trusted list.")
        return False
    accounts.remove(username)
    config["trusted_x_accounts"] = accounts
    if save_config(config):
        print(f"🗑️  @{username} removed from trusted accounts.")
        return True
    return False


# --- Fetching ------------------------------------------------------------

def fetch_account_posts(username: str, count: int = 20) -> list[dict[str, Any]]:
    """Fetch recent crypto-relevant posts (>= min likes) from an account."""
    client = _get_client()
    if client is None:
        return []

    config = load_config()
    min_likes = int(config.get("x_min_likes", 10))
    username = username.lstrip("@")

    try:
        user = client.get_user(username=username)
        if not getattr(user, "data", None):
            print(f"❌ [X] @{username} not found.")
            return []
        resp = client.get_users_tweets(
            user.data.id,
            max_results=min(max(count, 5), 100),
            tweet_fields=["public_metrics", "created_at"],
            exclude=["retweets", "replies"],
        )
    except Exception as exc:
        print(f"⚠️  [X] Fetch failed for @{username}: {exc}")
        return []

    tweets = getattr(resp, "data", None) or []
    posts: list[dict[str, Any]] = []
    for t in tweets:
        metrics = getattr(t, "public_metrics", None) or {}
        likes = metrics.get("like_count", 0)
        if likes < min_likes:
            continue
        if not _has_keyword(t.text):
            continue
        posts.append({
            "post_id": str(t.id),
            "username": username,
            "text": t.text,
            "likes": likes,
            "retweets": metrics.get("retweet_count", 0),
            "date": str(getattr(t, "created_at", "")),
            "url": f"https://x.com/{username}/status/{t.id}",
        })
    print(f"🐦 [X] @{username}: {len(posts)} relevant posts "
          f"(>= {min_likes} likes).")
    return posts


# --- Scraping + extraction ----------------------------------------------

def _extract_from_posts(username: str, posts: list[dict[str, Any]]
                        ) -> Optional[StrategyCard]:
    """Combine an account's posts and run the strategy extractor (auto mode)."""
    if not posts:
        return None
    combined = "\n\n".join(f"- {p['text']}" for p in posts)
    # force auto mode so batch scraping never blocks on manual input.
    card = extract_strategy(combined, source_type="twitter",
                            source_url=f"@{username}", force_mode="auto")
    if card and card.confidence_score > 0:
        strategy_store.save_card(card)
        return card
    return None


def scrape_single_account(username: str) -> dict[str, Any]:
    """Scrape one account; extract + save any strategy found."""
    count = int(load_config().get("x_posts_to_fetch", 20))
    posts = fetch_account_posts(username, count)
    card = _extract_from_posts(username.lstrip("@"), posts)
    found = 1 if card else 0
    print(f"@{username.lstrip('@')} → {len(posts)} posts fetched, "
          f"{found} strategies found")
    return {"username": username.lstrip("@"), "posts": len(posts),
            "strategies_found": found,
            "strategy": card.to_dict() if card else None}


def scrape_all_trusted_accounts() -> dict[str, Any]:
    """Scrape every trusted account; return a summary of what was found."""
    accounts = list_trusted_accounts()
    if not accounts:
        print("ℹ️  [X] No trusted accounts configured.")
        return {"accounts_checked": 0, "new_strategies": 0, "found": []}

    total_found = 0
    found_list: list[dict[str, str]] = []
    for username in accounts:
        result = scrape_single_account(username)
        if result["strategies_found"]:
            total_found += 1
            found_list.append({
                "name": result["strategy"]["name"],
                "username": f"@{result['username']}",
            })

    print(f"🐦 [X] Scrape complete: {len(accounts)} accounts, "
          f"{total_found} new strategies.")
    return {"accounts_checked": len(accounts), "new_strategies": total_found,
            "found": found_list}
