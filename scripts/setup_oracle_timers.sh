#!/usr/bin/env bash
#
# StrategyHarvester — install the periodic systemd timers (Oracle Cloud).
# Run once, after scripts/setup_oracle.sh, from inside the project dir:
#   chmod +x scripts/setup_oracle_timers.sh && ./scripts/setup_oracle_timers.sh
#
# Installs two independent timer/service pairs:
#   oracle_scan.{service,timer}           price scanner, every 30 min
#   oracle_content_intel.{service,timer}  content-intelligence watchlist, 4h
#
# Both call the SAME headless entry points GitHub Actions uses
# (scheduler.runner_cron / scheduler.content_intelligence_cron) — this is
# what gives feature parity with the GH Actions workflows (dashboard
# generation, scan_runs stats, checkpointed watchlist processing, etc.),
# unlike the older continuous-loop scheduler.runner_prod design.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(whoami)"

install_pair() {
  local name="$1"
  echo "==> Installing ${name}.service / ${name}.timer..."
  for ext in service timer; do
    TMP="$(mktemp)"
    sed -e "s#__USER__#${RUN_USER}#g" \
        -e "s#__PROJECT_DIR__#${PROJECT_DIR}#g" \
        "$PROJECT_DIR/scripts/${name}.${ext}" > "$TMP"
    sudo cp "$TMP" "/etc/systemd/system/${name}.${ext}"
    rm -f "$TMP"
  done
}

install_pair "oracle_scan"
install_pair "oracle_content_intel"

sudo systemctl daemon-reload
sudo systemctl enable --now oracle_scan.timer
sudo systemctl enable --now oracle_content_intel.timer

cat <<EOF

===================================================================
 Timers installed and started.

 Price scanner   : every 30 min  (systemctl status oracle_scan.timer)
 Content intel   : every 4 hours (systemctl status oracle_content_intel.timer)

 Trigger a run immediately without waiting for the schedule:
   sudo systemctl start oracle_scan.service
   sudo systemctl start oracle_content_intel.service

 Watch logs:
   journalctl -u oracle_scan.service -f
   journalctl -u oracle_content_intel.service -f

 List all scheduled timers + next run time:
   systemctl list-timers
===================================================================
EOF
