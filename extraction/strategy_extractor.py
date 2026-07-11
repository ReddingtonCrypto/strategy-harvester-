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

# Claude model used for SUBSCRIPTION mode (same tier as AUTO — Haiku).
_SUBSCRIPTION_MODEL = "claude-haiku-4-5-20251001"

# Higher-quality model for callers that explicitly want it (the dashboard's
# manual-paste routes — transcript/text/image/single-message).
OPUS_MODEL = "claude-opus-4-8"

# Set by _extract_auto/_extract_subscription right before they return None on
# a real failure (CLI missing, API error, etc — NOT a legitimate "no strategy
# in this content" result). Callers that only see extract_strategy() return
# None can check this to tell the two cases apart and surface a real error
# instead of a generic "nothing found" message. Reset at the top of every
# extract_strategy() call.
LAST_ERROR: Optional[str] = None


def _set_error(msg: str) -> None:
    global LAST_ERROR
    LAST_ERROR = msg
    print(f"❌ [Extractor] {msg}")

_VALID_MODES = ("manual", "auto", "subscription")


def build_prompt(content: str) -> str:
    """Return the full extraction prompt with content injected."""
    return EXTRACTION_PROMPT.replace("[CONTENT]", content or "")


def _resolve_mode(force_mode: Optional[str], config: dict,
                  api_key: Optional[str]) -> str:
    """Decide which extraction mode to use, in priority order:

    1. An explicit `force_mode` argument (headless callers, tests).
    2. config.json's `"extraction_mode"` key, if set to a valid value —
       the primary switch: edit config.json to move between "manual",
       "auto", and "subscription" without touching code.
    3. Legacy fallback for configs written before `extraction_mode`
       existed: the original CLAUDE_API_KEY + `manual_mode` boolean check.
    """
    if force_mode in _VALID_MODES:
        return force_mode
    configured = config.get("extraction_mode")
    if configured in _VALID_MODES:
        return configured
    if api_key and not config.get("manual_mode", True):
        return "auto"
    return "manual"


def extract_strategy(
    raw_text: str,
    *,
    source_type: str = "manual",
    source_url: str = "",
    force_mode: Optional[str] = None,
    model: Optional[str] = None,
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
    model : str | None
        Override the Claude model used (AUTO/SUBSCRIPTION modes only).
        Defaults to the fast Haiku tier when omitted — callers that want
        higher-quality extraction (e.g. the dashboard's manual-paste
        routes) pass an explicit model like "claude-opus-4-8".

    Returns
    -------
    StrategyCard | None
        The extracted card, or None if extraction was cancelled/failed.
    """
    global LAST_ERROR
    LAST_ERROR = None

    if not raw_text or not raw_text.strip():
        print("⚠️  [Extractor] No content to analyse.")
        return None

    config = load_config()
    api_key = get_env("CLAUDE_API_KEY")
    mode = _resolve_mode(force_mode, config, api_key)

    print(f"🧩 [Extractor] Running in {mode.upper()} mode.")

    if mode == "auto":
        extracted = _extract_auto(raw_text, api_key, model=model)
    elif mode == "subscription":
        extracted = _extract_subscription(raw_text, model=model)
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

def _extract_auto(raw_text: str, api_key: str, *, model: Optional[str] = None
                  ) -> Optional[dict]:
    """Call the Claude API and parse the JSON response."""
    try:
        import anthropic
    except ImportError:
        _set_error("'anthropic' not installed — cannot use AUTO mode.")
        return None

    prompt = build_prompt(raw_text)
    try:
        print("🤖 [Extractor] Calling Claude API...")
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model or _DEFAULT_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate any text blocks in the response.
        text = "".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        )
    except Exception as exc:
        _set_error(f"Claude API call failed: {exc}")
        return None

    parsed = extract_json(text)
    if parsed is None:
        _set_error("Could not parse JSON from Claude's response.")
        return None
    return parsed


# --- SUBSCRIPTION mode -----------------------------------------------------

def _extract_subscription(raw_text: str, *, model: Optional[str] = None
                          ) -> Optional[dict]:
    """Call the local `claude` CLI headlessly instead of a billed API key.

    Uses whatever auth the CLI already has configured on this machine — an
    existing interactive `claude` login locally, or a
    CLAUDE_CODE_OAUTH_TOKEN environment variable in CI (see
    scheduler/content_intelligence_cron.py). CLAUDE_API_KEY /
    ANTHROPIC_API_KEY are explicitly stripped from the subprocess's
    environment so this mode can never fall back to a billed API key by
    accident.
    """
    import json
    import os
    import shutil
    import subprocess

    if shutil.which("claude") is None:
        _set_error("'claude' CLI not found on PATH — cannot use SUBSCRIPTION "
                   "mode. Install Claude Code, or switch \"extraction_mode\" "
                   "in config.json to \"manual\" or \"auto\".")
        return None

    prompt = build_prompt(raw_text)
    env = dict(os.environ)
    env.pop("CLAUDE_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)

    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--model", model or _SUBSCRIPTION_MODEL]
    try:
        print("🔑 [Extractor] Calling local Claude CLI (subscription mode)...")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            env=env, check=False)
    except subprocess.TimeoutExpired:
        _set_error("Claude CLI call timed out after 120s.")
        return None
    except OSError as exc:
        _set_error(f"Could not run the Claude CLI: {exc}")
        return None

    # `claude -p ... --output-format json` prints a JSON envelope to stdout
    # EVEN ON FAILURE (e.g. not logged in) — the useful error text is in the
    # envelope's "result" field with "is_error": true, not in stderr/the
    # process exit code. So: always try to parse stdout as JSON first, and
    # only fall back to the exit code/stderr if stdout wasn't JSON at all
    # (verified against a real un-authenticated CLI call: exit code 1,
    # stdout = {"type":"result","is_error":true,"result":"Not logged in "
    # "· Please run /login", ...}).
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        _set_error(f"Claude CLI exited {result.returncode} with non-JSON "
                   f"output: {(result.stderr or result.stdout).strip()[:500]}")
        return None

    if isinstance(envelope, dict) and envelope.get("is_error"):
        _set_error(f"Claude CLI reported an error: "
                   f"{envelope.get('result', '(no message)')}")
        return None

    # The actual reply text is under "result".
    text = envelope.get("result", "") if isinstance(envelope, dict) else ""
    parsed = extract_json(text)
    if parsed is None:
        _set_error("Could not parse a strategy from the CLI response.")
        return None
    return parsed
