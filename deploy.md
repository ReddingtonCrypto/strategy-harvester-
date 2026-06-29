# Deploying StrategyHarvester on Oracle Cloud (Always Free)

This runs the **24/7 scanner + learning engine** on a free Oracle Cloud VM —
free forever, no credit-card charges on the Always-Free tier. The engine only
makes **outbound** calls (Binance, Telegram, Claude), so no inbound ports are
required.

> What runs in the cloud: the live scanner, signal outcome tracking, daily
> learning/adaptation, X scraping, and the daily Top-50 refresh. Ingestion tools
> (YouTube/Whisper/images) and the REST API stay on your local machine.

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

**Nothing to open.** The engine is outbound-only. (The health endpoint listens
on `:8080` but you only need it locally — leave the firewall closed unless you
want to reach it remotely.)

## Step 4 — SSH into the VM

```bash
ssh -i /path/to/your_key ubuntu@<PUBLIC_IP>
```

## Step 5 — Get the project onto the VM and run setup

Copy the project up (from your local machine):
```bash
# option A: scp a zip
scp -i your_key strategy_harvester.zip ubuntu@<PUBLIC_IP>:~/
# then on the VM:  unzip strategy_harvester.zip
# option B: git clone <your repo>   (if you've pushed it somewhere)
```
Then on the VM:
```bash
cd ~/strategy_harvester
chmod +x scripts/setup_oracle.sh
./scripts/setup_oracle.sh
```
This installs Python 3.11, creates the venv, installs `requirements_prod.txt`,
creates runtime folders, copies `.env` from the template, and installs +
enables the `stratharv` systemd service.

## Step 6 — Fill in your secrets

```bash
nano ~/strategy_harvester/.env
```
Required for live alerts: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`BINANCE_API_KEY`/`BINANCE_API_SECRET`. For daily learning: `CLAUDE_API_KEY`.
Optional: the `X_*` keys for sentiment/scraping. Save with `Ctrl+O`, `Ctrl+X`.

## Step 7 — Start the service

```bash
sudo systemctl start stratharv
```
It also auto-starts on boot and auto-restarts on failure (10 s delay).

## Step 8 — Watch the logs

```bash
journalctl -u stratharv -f
```
You should see the startup banner, `👂 Approval listener active`, the health
endpoint line, and the first scan running across the Top-50 coins.

## Step 9 — Verify Telegram

Check your phone for the **"🚀 Signal Engine started"** message. That confirms
the bot token + chat id are correct and live alerts will arrive.

---

## Operating it

| Action | Command |
|---|---|
| Status | `systemctl status stratharv` |
| Start / stop / restart | `sudo systemctl {start,stop,restart} stratharv` |
| Live logs | `journalctl -u stratharv -f` |
| Health JSON | `curl http://localhost:8080/` |
| Update config (coins, thresholds) | edit `config.json`, then `sudo systemctl restart stratharv` |

The health endpoint returns:
```json
{"status":"alive","version":"5.6","last_scan":"2026-…","signals_today":0,"uptime":"3h 12m"}
```

## Notes
- **Logs:** captured by journald *and* written to `logs/stratharv_YYYYMMDD.log`
  (files older than 7 days auto-deleted).
- **DB safety:** the SQLite DB is auto-backed-up to `backups/` (last 10) on every
  startup before any migration.
- **Clean shutdown:** `systemctl stop` sends SIGTERM → the engine stops the
  approval listener and sends a Telegram shutdown notice.
- **Resilience:** a crash in the engine loop is caught, alerted to Telegram, and
  the loop restarts after 30 s; a single coin's fetch failure is skipped, never
  crashing the scan.
