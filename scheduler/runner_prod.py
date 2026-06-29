"""
Production entry point (cloud / 24-7).

Runs ONLY the scanner + learning engine — no menu, no interactive input.
Designed for Oracle Cloud (Ubuntu) under systemd:

  python -m scheduler.runner_prod

Features
--------
- Loads .env from the project root regardless of CWD.
- Logs everything to stdout (journald captures it) AND a daily-rotated file
  under logs/ (files older than 7 days are auto-deleted).
- Handles SIGTERM/SIGINT for a clean shutdown (Telegram notice + listener stop).
- Restarts the engine loop on any unhandled exception (never dies silently).
- Exposes a tiny health-check HTTP endpoint on :8080 for uptime monitoring.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "5.6"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
HEALTH_PORT = 8080
_START_TIME = time.time()
_RESTART_DELAY = 30  # seconds to wait before restarting after a crash


# --- stdout tee → console + daily-rotated file --------------------------

class _DailyLogTee:
    """Write output to the real stream AND a per-day log file (7-day retain)."""

    def __init__(self, stream):
        self._stream = stream
        self._date = None
        self._fh = None
        LOG_DIR.mkdir(exist_ok=True)

    def _rollover(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if today != self._date:
            if self._fh:
                try:
                    self._fh.close()
                except Exception:
                    pass
            self._date = today
            self._fh = open(LOG_DIR / f"stratharv_{today}.log", "a",
                            encoding="utf-8", errors="replace")
            _prune_old_logs()

    def write(self, data):
        self._rollover()
        try:
            self._stream.write(data)
        except Exception:
            pass
        try:
            self._fh.write(data)
        except Exception:
            pass
        return len(data)

    def flush(self):
        for t in (self._stream, self._fh):
            try:
                t.flush()
            except Exception:
                pass


def _prune_old_logs(days: int = 7) -> None:
    """Delete log files older than `days` to keep the VM disk clean."""
    cutoff = time.time() - days * 86400
    try:
        for f in LOG_DIR.glob("stratharv_*.log"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def _setup_logging() -> None:
    """Make stdout UTF-8 (emoji-safe) then tee it to a rotating daily file."""
    for s in ("stdout", "stderr"):
        try:
            getattr(sys, s).reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.stdout = _DailyLogTee(sys.stdout)
    sys.stderr = _DailyLogTee(sys.stderr)


# --- Health check endpoint ----------------------------------------------

def _health_state() -> dict:
    """Build the health/status payload (queried per request)."""
    from signals import signal_store
    from storage import database as db

    uptime_s = int(time.time() - _START_TIME)
    last_scan = None
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(recorded_at) FROM regime_history").fetchone()
            last_scan = row[0] if row else None
    except Exception:
        last_scan = None
    try:
        signals_today = len(signal_store.get_signals_today())
    except Exception:
        signals_today = 0
    return {
        "status": "alive",
        "version": VERSION,
        "last_scan": last_scan,
        "signals_today": signals_today,
        "uptime": f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m",
        "uptime_seconds": uptime_s,
    }


def _start_health_server() -> None:
    """Start a minimal JSON health endpoint on :8080 (daemon thread)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            payload = json.dumps(_health_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence default access logging
            return

    def serve():
        try:
            ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), Handler).serve_forever()
        except Exception as exc:
            print(f"⚠️  [Health] server failed to start on :{HEALTH_PORT}: {exc}")

    threading.Thread(target=serve, name="health", daemon=True).start()
    print(f"❤️  Health endpoint on http://0.0.0.0:{HEALTH_PORT}/")


# --- Signal handling -----------------------------------------------------

def _install_signal_handlers() -> None:
    """Translate SIGTERM/SIGINT into KeyboardInterrupt for clean shutdown."""
    def handler(signum, _frame):
        name = signal.Signals(signum).name
        print(f"\n🛑 [Prod] Received {name} — shutting down cleanly...")
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported


# --- Main ----------------------------------------------------------------

def _banner() -> None:
    from utils.helpers import load_config
    coins = load_config().get("default_assets", [])
    print("=" * 60)
    print(f"  StrategyHarvester v{VERSION} — PRODUCTION (cloud 24/7)")
    print(f"  Scanner + learning engine only · {len(coins)} coins watched")
    print(f"  Started {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 60)


def main() -> None:
    _setup_logging()
    # Load .env from the project root no matter where we're launched from.
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        print("⚠️  python-dotenv not installed; relying on process env vars.")

    from utils import helpers  # noqa: F401 (triggers console setup)
    from storage import strategy_store

    _install_signal_handlers()
    _banner()
    strategy_store.init()
    _start_health_server()

    from scheduler.runner import start_scheduler
    from alerts import telegram_alert

    # Restart loop: a crash in the engine should never end the process.
    while True:
        try:
            start_scheduler()        # blocks; returns only on clean shutdown
            print("✅ [Prod] Engine stopped cleanly. Exiting.")
            return
        except KeyboardInterrupt:
            print("✅ [Prod] Clean shutdown complete.")
            return
        except Exception as exc:     # noqa: BLE001 — keep the process alive
            print(f"❌ [Prod] Engine crashed: {exc!r}")
            try:
                if telegram_alert.is_configured():
                    telegram_alert.send_error_alert(
                        f"Engine crashed: {exc}. Restarting in "
                        f"{_RESTART_DELAY}s.")
            except Exception:
                pass
            print(f"♻️  [Prod] Restarting engine in {_RESTART_DELAY}s...")
            time.sleep(_RESTART_DELAY)


if __name__ == "__main__":
    main()
