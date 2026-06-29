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

echo "==> Installing systemd service 'stratharv'..."
TMP="$(mktemp)"
sed -e "s#__USER__#${RUN_USER}#g" \
    -e "s#__PROJECT_DIR__#${PROJECT_DIR}#g" \
    "$PROJECT_DIR/scripts/oracle_systemd.service" > "$TMP"
sudo cp "$TMP" /etc/systemd/system/stratharv.service
rm -f "$TMP"
sudo systemctl daemon-reload
sudo systemctl enable stratharv

cat <<EOF

===================================================================
 Setup complete.

 1) Add your secrets:   nano ${PROJECT_DIR}/.env
       (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BINANCE_API_KEY/SECRET,
        CLAUDE_API_KEY, and optionally the X_* keys)

 2) Start the engine:   sudo systemctl start stratharv
 3) Follow the logs:    journalctl -u stratharv -f
 4) Health check:       curl http://localhost:8080/

 The service auto-starts on boot and auto-restarts on failure.
===================================================================
EOF
