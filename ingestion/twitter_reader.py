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


def process_watchlist(*, extraction_mode: Optional[str] = None) -> dict[str, Any]:
    """Process every active X source in the `sources` table (Phase 2).

    Same checkpoint pattern as ingestion/youtube_reader.py and
    ingestion/telegram_reader.py — but keyed on this table's `sources`
    entries, NOT config.json's `trusted_x_accounts` list (that older,
    separate mechanism — list_trusted_accounts()/scrape_all_trusted_accounts()
    below — is untouched and still works independently via menu option 18).

    Uses the X API's `since_id` for checkpointing (tweet ids are k-sortable,
    same idea as Telegram's sequential message ids). A failure on one source
    is logged and does not stop the others. Headless-safe: no input().
    """
    from storage import database as db, strategy_store
    from utils.helpers import utc_now_str

    summary: dict[str, Any] = {"sources_checked": 0, "posts_processed": 0,
                               "strategies_found": 0, "errors": [],
                               "skipped_reason": None}

    client = _get_client()
    if client is None:
        msg = ("X API credentials missing or tweepy not installed — "
               "skipping X watchlist processing this run.")
        print(f"⚠️  [X] {msg}")
        summary["skipped_reason"] = msg
        return summary

    sources = db.list_sources(source_type="twitter", active_only=True)
    if not sources:
        print("ℹ️  [X] No active X sources in the watchlist.")
        return summary

    config = load_config()
    min_likes = int(config.get("x_min_likes", 10))
    fetch_count = int(config.get("x_posts_to_fetch", 20))

    for source in sources:
        summary["sources_checked"] += 1
        username = source["identifier"].lstrip("@")
        label = source.get("label") or username
        print(f"🐦 [X] Checking watchlist source: {label} (@{username})")
        try:
            user = client.get_user(username=username)
            if not getattr(user, "data", None):
                print(f"   ❌ @{username} not found on X.")
                continue

            checkpoint = source.get("last_item_id")
            kwargs: dict[str, Any] = {
                "max_results": min(max(fetch_count, 5), 100),
                "tweet_fields": ["public_metrics", "created_at"],
                "exclude": ["retweets", "replies"],
            }
            if checkpoint:
                kwargs["since_id"] = checkpoint
            resp = client.get_users_tweets(user.data.id, **kwargs)
            tweets = getattr(resp, "data", None) or []

            if not tweets:
                print("   – no new posts since last check.")
                db.update_source_checkpoint(source["id"], utc_now_str())
                continue

            newest_id = max(int(t.id) for t in tweets)
            relevant: list[str] = []
            for t in tweets:
                metrics = getattr(t, "public_metrics", None) or {}
                if metrics.get("like_count", 0) < min_likes:
                    continue
                if not _has_keyword(t.text):
                    continue
                relevant.append(t.text)

            # Advance the checkpoint regardless of relevance — "checked",
            # not "found something" (same semantics as the other readers).
            db.update_source_checkpoint(source["id"], utc_now_str(), str(newest_id))

            if not relevant:
                print(f"   – {len(tweets)} new post(s), none relevant "
                      f"(< {min_likes} likes or no keyword match).")
                continue

            print(f"   {len(relevant)} relevant new post(s) — extracting...")
            combined = "\n\n".join(f"- {tx}" for tx in relevant)
            card = extract_strategy(combined, source_type="twitter",
                                    source_url=f"@{username}",
                                    force_mode=extraction_mode)
            if card and card.confidence_score > 0:
                strategy_store.save_card(card)
                summary["strategies_found"] += 1
                print(f"   ✅ strategy found: {card.name}")
            summary["posts_processed"] += len(relevant)
        except Exception as exc:  # noqa: BLE001 — one bad source shouldn't
            # stop the rest of the watchlist.
            print(f"   ❌ Source failed: {exc}")
            summary["errors"].append(f"{username}: {exc}")
            continue

    return summary


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
