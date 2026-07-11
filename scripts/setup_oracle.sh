#!/usr/bin/env bash
#
# StrategyHarvester — Oracle Cloud (Ubuntu 22.04) setup.
# Run once on a fresh Always-Free Ampere VM, from inside the project dir:
#   chmod +x scripts/setup_oracle.sh && ./scripts/setup_oracle.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(whoami)"
PY=python3.11

echo "==> Updating apt (this can take a minute)..."
sudo apt-get update -y
sudo apt-get upgrade -y

echo "==> Installing prerequisites: python3.11, pip, venv, git..."
sudo apt-get install -y software-properties-common git curl
# Ubuntu 22.04 ships Python 3.10; pandas/numpy here need >= 3.11.
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev

echo "==> Project: $PROJECT_DIR   User: $RUN_USER"
cd "$PROJECT_DIR"

echo "==> Creating virtualenv + installing production requirements..."
$PY -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements_prod.txt

echo "==> Preparing runtime folders..."
mkdir -p logs data/cache backups

echo "==> Preparing .env..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Created .env from template — YOU MUST EDIT IT before starting."
else
  echo "    .env already exists; leaving it untouched."
fi

echo "==> Base setup complete. Systemd timers are installed separately —"
echo "    see scripts/setup_oracle_timers.sh (run it next)."

cat <<EOF

===================================================================
 Base setup complete.

 1) Add your secrets:   nano ${PROJECT_DIR}/.env
       (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BINANCE_API_KEY/SECRET,
        CLAUDE_API_KEY, and optionally the X_* keys)

 2) Install + start the scheduled timers:
       chmod +x scripts/setup_oracle_timers.sh
       ./scripts/setup_oracle_timers.sh

 This runs the price scanner every 30 min and the content-intelligence
 watchlist every 4 hours via systemd timers (the same headless entry
 points scanner.yml / content_intelligence.yml use in GitHub Actions) —
 no continuous service, no :8080 health endpoint needed.
===================================================================
EOF
