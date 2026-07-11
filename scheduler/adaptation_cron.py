"""
Headless entry point for the daily learning/adaptation run (Phase 4).

Runs ONE pass of learning.adaptation_engine.run_daily_adaptation(): updates
performance stats, runs parameter optimization, and generates improvement
suggestions (sent to Telegram for approval) for every PASSED strategy.

Claude is only used inside the pattern-finding step (learning/pattern_finder.py)
— everything else is pure Python. Without CLAUDE_API_KEY set, pattern_finder
already degrades to a heuristic fallback rather than failing (see
pattern_finder.py:73-80), so this is safe to run even before a Claude API key
is configured — performance tracking and parameter optimization still work,
only the LLM-authored pattern narrative is skipped.

Designed to be triggered by a scheduler (systemd timer on the Oracle VM, or a
GitHub Actions workflow); also runnable locally:

    python -m scheduler.adaptation_cron
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Load .env from the project root when present (mirrors runner_cron.py)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("⚠️  python-dotenv not installed; relying on process env vars.")
        return
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        print("ℹ️  No .env file — relying on process env vars (CI mode).")


def main() -> int:
    _load_env()

    from storage import strategy_store

    try:
        strategy_store.init()
    except Exception as exc:
        print(f"❌ [Adaptation] Could not initialise the database: {exc}")
        return 1

    print("=" * 70)
    print("StrategyHarvester — Daily Learning + Adaptation run")
    print("=" * 70)

    from learning.adaptation_engine import run_daily_adaptation

    try:
        summary = run_daily_adaptation(send_telegram=True)
    except Exception as exc:  # noqa: BLE001 — never crash the scheduled run
        print(f"❌ [Adaptation] Daily adaptation run failed: {exc}")
        return 1

    print(f"\n✅ Run complete — {summary['strategies_updated']} strategies "
          f"updated, {len(summary['optimizations'])} optimizations, "
          f"{summary['suggestions']} suggestion(s) generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
