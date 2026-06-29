"""
Local video/audio file reader (Phase 5.6, Part A).

Transcribes a LOCAL media file with Whisper (local, free, offline) and returns
the transcript for the existing strategy extractor. Covers both downloaded
YouTube videos and screen-recorded Instagram reels.

Whisper loads audio from any container via ffmpeg, so a video file can be passed
directly. Models are cached on the E: drive (never C:).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from ingestion.base_reader import IngestionError
from utils.helpers import clean_text, load_config

VIDEO_FORMATS = {"mp4", "mov", "mkv", "avi", "webm"}
AUDIO_FORMATS = {"mp3", "wav", "m4a", "aac"}

# Whisper model cache kept on E: (alongside the project), not the C: home dir.
_MODEL_DIR = Path(__file__).resolve().parent.parent / ".whisper_models"


def read_local_media(file_path: str) -> Optional[dict[str, Any]]:
    """Transcribe a local media file and return transcript + source metadata.

    Returns a dict {text, source_type, source_label} or None on failure.
    `source_type` is 'local_video' or 'local_audio'.
    """
    try:
        path = _normalise_path(file_path)
    except IngestionError as exc:
        print(f"❌ {exc}")
        return None

    ext = path.suffix.lower().lstrip(".")
    is_video = ext in VIDEO_FORMATS
    source_type = "local_video" if is_video else "local_audio"

    # Size warning.
    size_mb = path.stat().st_size / (1024 * 1024)
    max_mb = float(load_config().get("max_video_size_mb", 500))
    if size_mb > max_mb:
        print(f"⚠️  File is {size_mb:.0f} MB (over {max_mb:.0f} MB). "
              f"Transcription may take a long time.")

    if shutil.which("ffmpeg") is None:
        print("⚠️  ffmpeg not found on PATH — Whisper needs it to read media.\n"
              "    Install it (https://ffmpeg.org/download.html) and ensure "
              "'ffmpeg' is on your PATH, then retry.")

    text = _transcribe(path, is_video)
    if not text:
        return None

    cleaned = clean_text(text)
    print(f"✅ [Media] Transcribed {len(cleaned)} characters from {path.name}.")
    return {
        "text": cleaned,
        "source_type": source_type,
        "source_label": path.name,
    }


def _normalise_path(raw: str) -> Path:
    """Clean a user/drag-drop path and validate it's a supported media file."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        raise IngestionError("No file path provided.")
    path = Path(raw).expanduser()
    if not path.exists():
        raise IngestionError(f"File not found: {path}")
    if not path.is_file():
        raise IngestionError(f"Not a file: {path}")
    ext = path.suffix.lower().lstrip(".")
    if ext not in VIDEO_FORMATS | AUDIO_FORMATS:
        supported = ", ".join(sorted(VIDEO_FORMATS | AUDIO_FORMATS))
        raise IngestionError(f"Unsupported format '.{ext}'. Supported: {supported}")
    return path


def _transcribe(path: Path, is_video: bool) -> Optional[str]:
    """Run local Whisper transcription on a media file."""
    try:
        import whisper  # openai-whisper
    except ImportError:
        print("❌ [Media] openai-whisper is not installed.\n"
              "    Install it into the project's venv (it pulls in torch):\n"
              "    .venv\\Scripts\\python.exe -m pip install openai-whisper")
        return None

    model_name = str(load_config().get("whisper_model", "base"))
    kind = "video" if is_video else "audio"
    print(f"🧠 [Media] Loading Whisper model '{model_name}' "
          f"and transcribing {kind}...")
    print("   Transcribing... this may take a few minutes for long videos.")

    try:
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model = whisper.load_model(model_name, download_root=str(_MODEL_DIR))
        result = model.transcribe(str(path))
        return result.get("text", "")
    except Exception as exc:
        print(f"❌ [Media] Transcription failed: {exc}\n"
              f"    (Most often this means ffmpeg is missing or the file is "
              f"corrupt.)")
        return None
