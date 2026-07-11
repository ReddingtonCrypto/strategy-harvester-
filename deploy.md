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

The scanner and content-intelligence timers are outbound-only — nothing to
open for those. **If you want the dashboard reachable from outside the VM**
(`oracle_dashboard.service`, a live FastAPI app on `:8080` — see below), open
port 8080 in **two** places (both are required — either one alone isn't
enough):
- **Oracle Cloud Console** (VCN-level firewall): **Networking → Virtual Cloud
  Networks → (your VCN) → Security Lists → Default Security List → Add
  Ingress Rule** — Source CIDR `0.0.0.0/0` (or your own IP for privacy),
  IP Protocol TCP, Destination Port 8080.
- **The VM's own iptables** (done automatically by
  `scripts/setup_oracle_timers.sh` if `oracle_dashboard.service` is
  installed — see Step 7).

The dashboard requires a login (`DASHBOARD_PASSPHRASE` in `.env` — Step 6) —
it can add/remove watchlist sources and trigger runs, not just display
stats, so unlike a purely read-only page it needs *some* protection. It
fails closed: with no passphrase set, nobody can log in at all.

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

Then install the content-intelligence and dashboard dependencies into the
same venv (the timer-install script in Step 7 does the dashboard ones
automatically, but content-intelligence's are needed before that):
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
`BINANCE_API_KEY`/`BINANCE_API_SECRET`. Required for the dashboard:
`DASHBOARD_PASSPHRASE` (anything you choose — it fails closed without one).
For daily learning: `CLAUDE_API_KEY` (optional — degrades to a heuristic
fallback without it, see `learning/pattern_finder.py`). For SUBSCRIPTION-mode
extraction: `CLAUDE_CODE_OAUTH_TOKEN`. For the Telegram content-intelligence
watchlist: `TELEGRAM_API_ID`/`API_HASH`/`PHONE` plus `TELEGRAM_SESSION_STRING`
(see SUMMARY_PHASE1.md for how to generate one). Optional: the `X_*` keys for
X watchlist ingestion. Save with `Ctrl+O`, `Ctrl+X`.

## Step 7 — Install and start the timers

```bash
chmod +x scripts/setup_oracle_timers.sh
./scripts/setup_oracle_timers.sh
```
This installs and starts three independent systemd timer/service pairs, plus
a persistent live dashboard server:
- `oracle_scan.timer` → `oracle_scan.service` — price scanner, every 30 min.
- `oracle_content_intel.timer` → `oracle_content_intel.service` — content
  watchlist (YouTube/Telegram/X), every 4 hours.
- `oracle_adaptation.timer` → `oracle_adaptation.service` — daily
  learning/adaptation run, 01:00 UTC.
- `oracle_dashboard.service` — always-on, a live FastAPI app
  (`monitoring/dashboard_server.py`) on `:8080`. Log in with
  `DASHBOARD_PASSPHRASE`; from there you can add/remove watchlist sources,
  see which strategies are live/shadow/not-running, browse recent
  content-intelligence activity, and trigger any of the three jobs above
  immediately instead of waiting for their schedule. Reachable at
  `http://<VM_PUBLIC_IP>:8080/` once you've also opened the port in the
  Oracle Cloud Console (Step 3).

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

| Action | Command / place |
|---|---|
| Add/remove watchlist sources, trigger a run, see strategy status | The dashboard at `:8080` (easiest) |
| List all timers + next run time | `systemctl list-timers` |
| Status | `systemctl status oracle_scan.timer` / `oracle_content_intel.timer` / `oracle_adaptation.timer` |
| Run immediately from the CLI (equivalent to the dashboard's buttons) | `sudo systemctl start oracle_scan.service` / `oracle_content_intel.service` / `oracle_adaptation.service` |
| Stop a timer | `sudo systemctl stop oracle_scan.timer` |
| Live logs | `journalctl -u oracle_scan.service -f` (or `oracle_dashboard.service` for the web app itself) |
| Update config (coins, thresholds) | edit `config.json` — picked up on the next scheduled run automatically, nothing to restart |
| Add a watchlist source from the CLI instead | `python main.py add-source --type youtube\|telegram\|twitter --identifier "..." --label "..."` |
| List watchlist sources from the CLI | `python main.py list-sources` |
| Change the dashboard passphrase | edit `DASHBOARD_PASSPHRASE` in `.env`, then `sudo systemctl restart oracle_dashboard.service` |

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
