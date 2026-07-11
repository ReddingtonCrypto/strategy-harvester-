"""
Image + text intelligence reader (Phase 5, Part A).

Accepts a chart or post screenshot plus optional text notes and sends BOTH
together to Claude Opus 4.8 (vision) to extract a trading strategy. Produces a
Strategy Card via the same pipeline as every other source.

Follows the BaseReader contract loosely (image input rather than plain text).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

from ingestion.base_reader import IngestionError
from models.strategy_card import StrategyCard
from storage import strategy_store
from utils.helpers import extract_json, load_config

# Extension → Claude media type.
_MEDIA_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif",
}

EXTRACTION_PROMPT = """\
You are a professional trading strategy analyst.
Analyze this image carefully.

This could be:
1. A price chart (look for: candlesticks, indicators,
   trend lines, support/resistance, chart patterns)
2. A social media post about trading
   (look for: strategy descriptions, entry/exit rules,
   indicator settings, trade setups)
3. Both combined

Also consider these user notes:
[USER_TEXT_NOTES]

Extract any trading strategy you can identify.
Be specific about:
- What indicators are visible or mentioned
- What the entry conditions appear to be
- What the exit conditions appear to be
- What timeframe if visible
- What asset if visible or mentioned

Respond ONLY in this JSON format, nothing else:
{
  "image_type": "chart / post / mixed / unknown",
  "strategy_found": true/false,
  "name": "",
  "indicators": [],
  "entry_conditions": [],
  "exit_conditions": [],
  "timeframe": "",
  "assets": [],
  "market_condition": "",
  "confidence_score": 0,
  "visual_observations": "what you saw in the image",
  "notes": "any assumptions or uncertainties"
}

If no clear strategy found: set strategy_found=false
and confidence_score=0.
"""


# --- Input gathering -----------------------------------------------------

def read_image_input() -> Optional[dict[str, Any]]:
    """Interactively gather an image path + optional notes; return image data.

    Returns a dict with image_path, image_base64, media_type, image_type,
    text_notes, source_type, source_label — or None if input was invalid.
    """
    raw = input("Enter image file path: ").strip()
    if not raw:
        print("⚠️  No path entered.")
        return None

    try:
        path = _normalise_path(raw)
        b64, media_type = _encode_image(path)
    except IngestionError as exc:
        print(f"❌ {exc}")
        return None

    print("Add any notes about this image\n"
          "(strategy hints, context, source):\n"
          "(press Enter to skip)")
    notes = input("> ").strip()

    return {
        "image_path": str(path),
        "image_base64": b64,
        "media_type": media_type,
        "image_type": "unknown",  # refined by extract_from_image
        "text_notes": notes,
        "source_type": "image_input",
        "source_label": f"image: {path.name}",
    }


def _normalise_path(raw: str) -> Path:
    """Clean a user/drag-drop path (strip quotes, handle backslashes)."""
    raw = raw.strip().strip('"').strip("'")
    path = Path(raw).expanduser()
    if not path.exists():
        raise IngestionError(f"File not found: {path}")
    if not path.is_file():
        raise IngestionError(f"Not a file: {path}")
    ext = path.suffix.lower().lstrip(".")
    supported = [s.lower() for s in
                 load_config().get("image_supported_formats",
                                   ["png", "jpg", "jpeg", "webp", "gif"])]
    if ext not in supported:
        raise IngestionError(
            f"Unsupported format '.{ext}'. Supported: {', '.join(supported)}")
    return path


def _encode_image(path: Path) -> tuple[str, str]:
    """Return (base64, media_type) for an image, warning if it's large."""
    data = path.read_bytes()
    max_mb = float(load_config().get("image_max_size_mb", 5))
    size_mb = len(data) / (1024 * 1024)
    if size_mb > max_mb:
        print(f"⚠️  Image is {size_mb:.1f} MB (over {max_mb} MB). "
              f"It may be rejected or slow to process.")
    media_type = _MEDIA_TYPES.get(path.suffix.lower().lstrip("."), "image/png")
    return base64.b64encode(data).decode("ascii"), media_type


# --- Claude vision -------------------------------------------------------

def _call_vision(b64: str, media_type: str, prompt: str) -> Optional[str]:
    """Send an image + prompt to the local `claude` CLI (subscription mode, free).

    Writes the image to a temp file and asks the CLI to Read it, rather than
    using CLAUDE_API_KEY, so image extraction stays free like every other
    ingestion path (see extraction/strategy_extractor.py's SUBSCRIPTION mode
    for the same envelope-parsing pattern). CLAUDE_API_KEY / ANTHROPIC_API_KEY
    are stripped from the subprocess env so this can never fall back to a
    billed key by accident.
    """
    import json
    import os
    import shutil
    import subprocess
    import tempfile
    import time as _time

    from extraction.strategy_extractor import _SUBSCRIPTION_MODEL

    if shutil.which("claude") is None:
        print("❌ [Image] 'claude' CLI not found on PATH — cannot use "
              "subscription mode. Install Claude Code to use this feature.")
        return None

    ext = media_type.split("/")[-1].replace("jpeg", "jpg")
    tmp_dir = Path(tempfile.gettempdir()) / "strategy_harvester_images"
    tmp_dir.mkdir(exist_ok=True)
    tmp_path = tmp_dir / f"upload_{int(_time.time() * 1000)}.{ext}"
    tmp_path.write_bytes(base64.b64decode(b64))

    cli_prompt = f"Read the image at {tmp_path} then: {prompt}"
    env = dict(os.environ)
    env.pop("CLAUDE_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)

    cmd = ["claude", "-p", cli_prompt, "--output-format", "json",
           "--model", _SUBSCRIPTION_MODEL, "--allowedTools", "Read"]
    try:
        print("🔑 [Image] Calling local Claude CLI (subscription mode)...")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, env=env, check=False)
    except subprocess.TimeoutExpired:
        print("❌ [Image] Claude CLI call timed out after 120s.")
        return None
    except OSError as exc:
        print(f"❌ [Image] Could not run the Claude CLI: {exc}")
        return None
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"❌ [Image] Claude CLI exited {result.returncode} with "
              f"non-JSON output: {(result.stderr or result.stdout).strip()[:500]}")
        return None

    if isinstance(envelope, dict) and envelope.get("is_error"):
        print(f"❌ [Image] Claude CLI reported an error: "
              f"{envelope.get('result', '(no message)')}")
        return None

    return envelope.get("result", "") if isinstance(envelope, dict) else ""


def detect_image_type(image_path: str) -> str:
    """Ask Claude whether an image is a chart, post, mixed, or unknown."""
    try:
        path = _normalise_path(image_path)
        b64, media_type = _encode_image(path)
    except IngestionError as exc:
        print(f"❌ {exc}")
        return "unknown"
    text = _call_vision(
        b64, media_type,
        "Is this a price chart or a social media post? Answer with ONLY one "
        "word: chart, post, mixed, or unknown.")
    if not text:
        return "unknown"
    word = text.strip().lower().split()[0] if text.strip() else "unknown"
    return word if word in {"chart", "post", "mixed", "unknown"} else "unknown"


def extract_from_image(image_data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Send image + user notes to Claude and return the parsed strategy JSON."""
    if not image_data:
        return None
    prompt = EXTRACTION_PROMPT.replace(
        "[USER_TEXT_NOTES]", image_data.get("text_notes") or "(none)")
    text = _call_vision(image_data["image_base64"],
                        image_data.get("media_type", "image/png"), prompt)
    if not text:
        print("❌ [Image] Claude could not read the image (no response).")
        return None
    parsed = extract_json(text)
    if parsed is None:
        print("❌ [Image] Could not parse a strategy from Claude's response.")
        return None
    return parsed


def build_and_save_card(parsed: dict[str, Any],
                        image_data: dict[str, Any]) -> Optional[StrategyCard]:
    """Build a Strategy Card from the vision result and save it to the DB."""
    if not parsed or not parsed.get("strategy_found"):
        print("ℹ️  [Image] No clear strategy found in the image.")
        return None

    raw = (image_data.get("text_notes", "")
           + "\n\n[VISUAL OBSERVATIONS]: "
           + str(parsed.get("visual_observations", ""))).strip()
    card = StrategyCard.from_extraction(
        parsed, source_type="image_input",
        source_url=image_data.get("source_label", "image"), raw_content=raw)
    strategy_store.save_card(card)
    return card
