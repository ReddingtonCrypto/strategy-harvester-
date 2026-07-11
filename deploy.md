# Deploying StrategyHarvester on Oracle Cloud (Always Free)

Runs the **price scanner** (every 30 min) and the **content-intelligence
watchlist** (every 4 hours) on a free Oracle Cloud VM — free forever, no
credit-card charges on the Always-Free tier. Both run as periodic systemd
timers calling the SAME headless entry points GitHub Actions uses
(`scheduler.runner_cron` / `scheduler.content_intelligence_cron`), so the VM
gets exactly the same behavior as the GitHub Actions workflows — dashboard
generation, `scan_runs` stats, checkpointed watchlist processing — with
nothing to reconcile between two divergent code paths.

> **Not a continuous service.** Earlier versions of this doc described a
> single always-running `systemctl start stratharv` service
> (`scheduler.runner_prod`). That path predates the dashboard/`scan_runs`
> instrumentation and the content-intelligence watchlist — it's kept in git
> history but no longer used. Use the timer-based setup below instead.

The engine only makes **outbound** calls (Binance, Telegram, Claude), so no
inbound ports are required.

---

## Step 1 — Create an Oracle Cloud account (free)

Go to <https://www.oracle.com/cloud/free/> and sign up. The **Always Free** tier
includes Ampere ARM (A1) compute — enough for this engine. (A card is used for
identity verification only; Always-Free resources are never charged.)

## Step 2 — Create the VM

In the console: **Compute → Instances → Create Instance**.
- **Shape:** `VM.Standard.A1.Flex` (Ampere ARM) — e.g. 1 OCPU / 6 GB RAM.
- **Image:** **Canonical Ubuntu 22.04**.
- **SSH keys:** upload/download a key pair (you'll SSH with this).
- Create. Note the instance's **public IP**.

## Step 3 — Networking

**Nothing to open.** Everything here is outbound-only — no health endpoint,
no inbound ports needed.

## Step 4 — SSH into the VM

```bash
ssh -i /path/to/your_key ubuntu@<PUBLIC_IP>
```

## Step 5 — Get the project onto the VM and run base setup

```bash
git clone https://github.com/ReddingtonCrypto/strategy-harvester-.git strategy_harvester
cd strategy_harvester
chmod +x scripts/setup_oracle.sh
./scripts/setup_oracle.sh
```
This installs Python 3.11, creates the venv, installs `requirements_prod.txt`,
creates runtime folders, and copies `.env` from the template.

Then install the content-intelligence dependencies into the same venv:
```bash
./.venv/bin/python -m pip install -r requirements_content_intel.txt
```

If you want SUBSCRIPTION-mode extraction (the config.json default —
`"extraction_mode"`) to work on this VM, also install the Claude Code CLI:
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g @anthropic-ai/claude-code
```

## Step 6 — Fill in your secrets

```bash
nano ~/strategy_harvester/.env
```
Required for live alerts: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`BINANCE_API_KEY`/`BINANCE_API_SECRET`. For daily learning: `CLAUDE_API_KEY`.
For SUBSCRIPTION-mode extraction: `CLAUDE_CODE_OAUTH_TOKEN`. For the Telegram
content-intelligence watchlist: `TELEGRAM_API_ID`/`API_HASH`/`PHONE` plus
`TELEGRAM_SESSION_STRING` (see SUMMARY_PHASE1.md for how to generate one).
Optional: the `X_*` keys for sentiment/scraping. Save with `Ctrl+O`, `Ctrl+X`.

## Step 7 — Install and start the timers

```bash
chmod +x scripts/setup_oracle_timers.sh
./scripts/setup_oracle_timers.sh
```
This installs and starts two independent systemd timer/service pairs:
- `oracle_scan.timer` → `oracle_scan.service` — price scanner, every 30 min.
- `oracle_content_intel.timer` → `oracle_content_intel.service` — content
  watchlist, every 4 hours.

## Step 8 — Watch the logs

```bash
journalctl -u oracle_scan.service -f
journalctl -u oracle_content_intel.service -f
```

## Step 9 — Verify Telegram

Once `oracle_scan.service` has run at least once, check your phone for scan
activity / heartbeat messages (per `heartbeat_enabled` in `config.json`) —
that confirms the bot token + chat id are correct.

---

## Operating it

| Action | Command |
|---|---|
| List all timers + next run time | `systemctl list-timers` |
| Status | `systemctl status oracle_scan.timer` / `oracle_content_intel.timer` |
| Run immediately (don't wait for the schedule) | `sudo systemctl start oracle_scan.service` / `oracle_content_intel.service` |
| Stop a timer | `sudo systemctl stop oracle_scan.timer` |
| Live logs | `journalctl -u oracle_scan.service -f` |
| Update config (coins, thresholds) | edit `config.json` — picked up on the next scheduled run automatically, nothing to restart |
| Add a YouTube/Telegram watchlist source | `python main.py add-source --type youtube --identifier "..." --label "..."` |
| List watchlist sources | `python main.py list-sources` |

## Notes
- **State persistence:** unlike the GitHub Actions workflows (which commit
  the DB back to git because runners are ephemeral), this VM's disk is
  persistent — `strategy_harvester.db` just lives on disk between runs. No
  git push needed for day-to-day operation. `git pull` when you want to
  deploy a code update.
- **DB safety:** the SQLite DB is auto-backed-up to `backups/` (last 10) on
  every run, before any migration.
- **Resilience:** each timer fires an independent one-shot run — a failure in
  one run (or one coin, or one watchlist source) doesn't affect the next
  scheduled run or any other source; see `journalctl` for per-run errors.
- **If you also run the GitHub Actions workflows**, disable them
  (`scanner.yml` / `content_intelligence.yml`) or you'll get two independent
  databases evolving separately — the VM's local disk and whatever GitHub
  Actions last committed to git will diverge.
