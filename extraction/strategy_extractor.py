"""
Strategy extractor.

Turns raw ingested text into a structured Strategy Card using one of two modes:

MANUAL mode (default, no API key needed)
    Prints the extraction prompt + content for the user to paste into a Claude
    chat, then accepts the pasted-back JSON response and parses it.

AUTO mode (when CLAUDE_API_KEY is set in .env)
    Calls the Claude API directly, parses the JSON, and returns the card.

Both modes produce the same validated `StrategyCard`.
"""

from __future__ import annotations

from typing import Optional

from models.strategy_card import StrategyCard
from utils.helpers import extract_json, get_env, load_config

# The exact extraction prompt the project specifies. `[CONTENT]` is replaced
# with the ingested text before sending/showing.
EXTRACTION_PROMPT = """\
You are a professional trading strategy analyst.
Read the content below carefully.
Extract any trading strategy you find.
Respond ONLY in valid JSON. No explanation. No markdown.

{
  "name": "",
  "indicators": [],
  "entry_conditions": [],
  "exit_conditions": [],
  "timeframe": "",
  "assets": [],
  "market_condition": "",
  "confidence_score": 0
}

If no strategy found: return confidence_score as 0.
Content:
[CONTENT]
"""

# Claude model used for AUTO mode (latest small, fast, capable model).
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def build_prompt(content: str) -> str:
    """Return the full extraction prompt with content injected."""
    return EXTRACTION_PROMPT.replace("[CONTENT]", content or "")


def extract_strategy(
    raw_text: str,
    *,
    source_type: str = "manual",
    source_url: str = "",
    force_mode: Optional[str] = None,
) -> Optional[StrategyCard]:
    """Extract a Strategy Card from raw text.

    Parameters
    ----------
    raw_text : str
        The ingested content to analyse.
    source_type : str
        'youtube' | 'telegram' | 'manual' — stored on the card.
    source_url : str
        Link or channel/source label — stored on the card.
    force_mode : 'manual' | 'auto' | None
        Override the mode chosen from config/.env. Mostly for testing.

    Returns
    -------
    StrategyCard | None
        The extracted card, or None if extraction was cancelled/failed.
    """
    if not raw_text or not raw_text.strip():
        print("⚠️  [Extractor] No content to analyse.")
        return None

    config = load_config()
    api_key = get_env("CLAUDE_API_KEY")

    # Decide mode: explicit override > API key presence + config flag.
    if force_mode in ("manual", "auto"):
        mode = force_mode
    elif api_key and not config.get("manual_mode", True):
        mode = "auto"
    elif api_key and config.get("manual_mode", True):
        # Key exists but config prefers manual — respect config, mention auto.
        print("ℹ️  [Extractor] CLAUDE_API_KEY found but manual_mode=true in "
              "config.json. Using MANUAL mode.")
        mode = "manual"
    else:
        mode = "manual"

    print(f"🧩 [Extractor] Running in {mode.upper()} mode.")

    if mode == "auto":
        extracted = _extract_auto(raw_text, api_key)
    else:
        extracted = _extract_manual(raw_text)

    if extracted is None:
        print("⚠️  [Extractor] No strategy extracted.")
        return None

    card = StrategyCard.from_extraction(
        extracted,
        source_type=source_type,  # type: ignore[arg-type]
        source_url=source_url,
        raw_content=raw_text,
    )

    # Warn if confidence is below the configured threshold.
    threshold = config.get("min_confidence_score", 60)
    if card.confidence_score < threshold:
        print(f"⚠️  [Extractor] Low confidence ({card.confidence_score} < "
              f"{threshold}). Review carefully before approving.")
    print(f"✅ [Extractor] Built Strategy Card: '{card.name}' "
          f"(confidence {card.confidence_score}).")
    return card


# --- MANUAL mode ---------------------------------------------------------

def _extract_manual(raw_text: str) -> Optional[dict]:
    """Print the prompt for the user, then parse their pasted Claude reply."""
    prompt = build_prompt(raw_text)

    print("\n" + "=" * 70)
    print("MANUAL EXTRACTION — copy everything below into a Claude chat:")
    print("=" * 70 + "\n")
    print(prompt)
    print("\n" + "=" * 70)
    print("Paste Claude's JSON response below. End with a line containing only")
    print("'END' (or press Ctrl-D / Ctrl-Z). Leave empty + END to cancel.")
    print("=" * 70)

    lines: list[str] = []
    try:
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
    except EOFError:
        pass  # Ctrl-D / Ctrl-Z ends input too.

    response = "\n".join(lines).strip()
    if not response:
        print("ℹ️  [Extractor] No response pasted — cancelled.")
        return None

    parsed = extract_json(response)
    if parsed is None:
        print("❌ [Extractor] Could not parse JSON from the pasted response.")
        return None
    return parsed


# --- AUTO mode -----------------------------------------------------------

def _extract_auto(raw_text: str, api_key: str) -> Optional[dict]:
    """Call the Claude API and parse the JSON response."""
    try:
        import anthropic
    except ImportError:
        print("❌ [Extractor] 'anthropic' not installed — cannot use AUTO mode.")
        return None

    prompt = build_prompt(raw_text)
    try:
        print("🤖 [Extractor] Calling Claude API...")
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=_DEFAULT_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate any text blocks in the response.
        text = "".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        )
    except Exception as exc:
        print(f"❌ [Extractor] Claude API call failed: {exc}")
        return None

    parsed = extract_json(text)
    if parsed is None:
        print("❌ [Extractor] Could not parse JSON from Claude's response.")
        return None
    return parsed
