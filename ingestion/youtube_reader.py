"""
YouTube content reader.

Strategy:
1. Try to pull an existing transcript with `youtube-transcript-api` (fast,
   free, no download).
2. If no transcript exists, fall back to downloading the audio with `yt-dlp`
   and transcribing locally with OpenAI Whisper (free, runs on CPU/GPU).

Returns a single cleaned plain-text string. Heavy/optional dependencies are
imported lazily inside methods so the rest of the app runs even if Whisper or
yt-dlp are not installed.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

from ingestion.base_reader import BaseReader, IngestionError
from utils.helpers import clean_text


class YouTubeReader(BaseReader):
    """Reader that turns a YouTube URL into transcript text."""

    source_type = "youtube"

    def read(self, url: str, whisper_model: str = "base") -> str:
        """Return the transcript text for a YouTube video URL.

        Parameters
        ----------
        url : str
            Full YouTube URL or bare video id.
        whisper_model : str
            Whisper model size to use for the fallback ('tiny', 'base',
            'small', ...). Smaller = faster, larger = more accurate.
        """
        print(f"🎬 [YouTube] Processing: {url}")
        video_id = self._extract_video_id(url)
        if not video_id:
            raise IngestionError(f"Could not parse a video id from URL: {url}")
        print(f"🔎 [YouTube] Video id: {video_id}")

        # --- Step 1: try captions/transcript ---
        text = self._try_transcript_api(video_id)
        if text:
            print("✅ [YouTube] Transcript found via youtube-transcript-api.")
            return clean_text(text)

        # --- Step 2: fall back to audio + Whisper ---
        print("ℹ️  [YouTube] No transcript available — falling back to Whisper.")
        text = self._transcribe_with_whisper(url, whisper_model)
        if text:
            print("✅ [YouTube] Transcript produced via Whisper.")
            return clean_text(text)

        raise IngestionError("Failed to obtain transcript by any method.")

    # ----- Internals -----------------------------------------------------

    @staticmethod
    def _extract_video_id(url: str) -> str | None:
        """Extract the 11-char video id from common YouTube URL formats."""
        if not url:
            return None
        url = url.strip()
        # Bare id passed directly.
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
            return url
        patterns = [
            r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _try_transcript_api(video_id: str) -> str | None:
        """Attempt to fetch a transcript; return text or None on failure."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            print("⚠️  [YouTube] youtube-transcript-api not installed.")
            return None

        try:
            # Works across library versions: list segments then join text.
            segments = YouTubeTranscriptApi.get_transcript(video_id)
            return " ".join(seg.get("text", "") for seg in segments)
        except Exception as exc:  # library raises several exception types
            print(f"ℹ️  [YouTube] Transcript API unavailable: {exc}")
            return None

    @staticmethod
    def _transcribe_with_whisper(url: str, model_name: str) -> str | None:
        """Download audio with yt-dlp and transcribe it with Whisper."""
        try:
            import whisper  # openai-whisper
            import yt_dlp
        except ImportError:
            print("⚠️  [YouTube] yt-dlp/openai-whisper not installed — "
                  "cannot transcribe. Install them or use a video with captions.")
            return None

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.%(ext)s"
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": str(audio_path),
                "quiet": True,
                "noprogress": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                    }
                ],
            }
            try:
                print("⬇️  [YouTube] Downloading audio with yt-dlp...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as exc:
                print(f"❌ [YouTube] Audio download failed: {exc}")
                return None

            # Find the produced audio file (extension set by post-processor).
            files = list(Path(tmp).glob("audio.*"))
            if not files:
                print("❌ [YouTube] No audio file was produced.")
                return None

            try:
                print(f"🧠 [YouTube] Transcribing with Whisper ('{model_name}')...")
                model = whisper.load_model(model_name)
                result = model.transcribe(str(files[0]))
                return result.get("text", "")
            except Exception as exc:
                print(f"❌ [YouTube] Whisper transcription failed: {exc}")
                return None


# Module-level convenience function for simple callers.
def read_youtube(url: str) -> str:
    """Shortcut: return transcript text for a YouTube URL."""
    return YouTubeReader().read(url)


# --- Bulk channel pull (Phase 5.6, Part B) ------------------------------

def fetch_channel_videos(channel_url: str) -> list[dict]:
    """List a channel's videos via yt-dlp (flat, no downloads).

    Accepts youtube.com/@name, /c/name, or /channel/UCxxxx. Returns a list of
    {url, title, id}, newest first as yt-dlp returns them.
    """
    try:
        import yt_dlp
    except ImportError:
        print("⚠️  [YouTube] yt-dlp not installed (pip install yt-dlp).")
        return []

    url = (channel_url or "").strip().rstrip("/")
    # Target the channel's Videos tab for a clean video list when possible.
    if not url.endswith("/videos") and ("/@" in url or "/channel/" in url
                                        or "/c/" in url or "/user/" in url):
        url = url + "/videos"

    opts = {"quiet": True, "extract_flat": True, "skip_download": True,
            "noprogress": True}
    try:
        print(f"🔎 [YouTube] Listing videos for {channel_url}...")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        print(f"❌ [YouTube] Could not list channel videos: {exc}")
        return []

    videos: list[dict] = []

    def _collect(entries) -> None:
        for e in entries or []:
            if not e:
                continue
            if e.get("entries"):          # nested playlists/tabs
                _collect(e["entries"])
                continue
            vid = e.get("id")
            if not vid:
                continue
            videos.append({
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": e.get("title", "") or "(untitled)",
                "id": vid,
            })

    _collect(info.get("entries"))
    # De-duplicate while preserving order.
    seen, unique = set(), []
    for v in videos:
        if v["id"] not in seen:
            seen.add(v["id"])
            unique.append(v)
    print(f"Found {len(unique)} videos in channel")
    return unique


def bulk_process_channel(channel_url: str, limit: Optional[int] = None,
                         use_whisper_fallback: bool = False) -> dict:
    """Pull a channel's videos, extract strategies, and save them.

    Skips videos already in the DB (when `skip_already_processed`). Returns a
    summary dict.
    """
    import time

    from extraction.strategy_extractor import extract_strategy
    from storage import strategy_store
    from utils.helpers import clean_text, load_config

    config = load_config()
    if limit is None:
        limit = int(config.get("bulk_channel_default_limit", 20))
    delay = float(config.get("delay_between_videos_seconds", 2))
    skip_existing = bool(config.get("skip_already_processed", True))
    whisper_model = str(config.get("whisper_model", "base"))

    videos = fetch_channel_videos(channel_url)
    if not videos:
        return {"checked": 0, "found": 0, "skipped": 0, "saved": []}
    videos = videos[: max(0, limit)]

    existing = set()
    if skip_existing:
        existing = {c.source_url for c in strategy_store.list_cards()}

    reader = YouTubeReader()
    found = skipped = 0
    saved: list[dict] = []

    for i, v in enumerate(videos, 1):
        print(f"[{i}/{len(videos)}] Processing: {v['title']}")
        if skip_existing and v["url"] in existing:
            print("   ⏭️  already in database; skipping")
            skipped += 1
            continue

        text = reader._try_transcript_api(v["id"])
        if not text and use_whisper_fallback:
            text = reader._transcribe_with_whisper(v["url"], whisper_model)
        if not text:
            print("   ⏭️  no transcript available; skipping")
            continue

        card = extract_strategy(clean_text(text), source_type="youtube",
                                source_url=v["url"], force_mode="auto")
        if card and card.confidence_score > 0:
            strategy_store.save_card(card)
            found += 1
            saved.append({"title": v["title"], "name": card.name})
            print(f"   ✅ strategy found ({found} total)")
        else:
            print("   – no strategy found in this video")

        if i < len(videos) and delay > 0:
            time.sleep(delay)  # be polite to avoid rate limits

    print("\n✅ Channel processed")
    print(f"   Videos checked   : {len(videos)}")
    print(f"   Already in DB    : {skipped}")
    print(f"   Strategies found : {found}")
    print("   Saved to database")
    print("   Run backtest via menu option 6")
    return {"checked": len(videos), "found": found, "skipped": skipped,
            "saved": saved}
