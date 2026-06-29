# Running StrategyHarvester on GitHub Actions (free cron)

This runs the scanner as a **scheduled job** instead of a 24/7 server. Every
hour, GitHub Actions spins up a fresh runner, executes **one scan cycle**
(`scheduler/runner_cron.py`), sends any Telegram alerts, then commits the
updated database back to the repo so state survives to the next run.

No server to maintain, no Oracle capacity issues, and it's free for public
repos (and within the free-minutes allowance for private ones).

---

## How state persists (important)

GitHub Actions runners are **stateless** — the filesystem is wiped after every
run. StrategyHarvester keeps its state (strategy cards, open signals, outcomes,
regime history) in a single SQLite file: `strategy_harvester.db`.

To survive between runs, the workflow **commits that DB back to the repo** at
the end of every run. The `.gitignore` is set up to ignore every `*.db` file
*except* `strategy_harvester.db` (backups and corrupt snapshots stay ignored).

> Trade-off: the repo accumulates one small commit per hour. That's fine — the
> DB is ~180 KB. If you ever want to reset history, squash or start a fresh repo.
> (An alternative is GitHub Actions `cache`, but a committed DB is simpler and
> visible, so we use that.)

---

## 1. Create the GitHub repo and push

The local repo is already initialized and committed. Create the remote and push:

```bash
cd E:/ISI/strategy_harvester

# Option A — with the GitHub CLI (easiest):
gh repo create strategy-harvester --private --source=. --remote=origin --push

# Option B — manually (after creating an empty repo in the GitHub web UI):
git remote add origin https://github.com/<YOUR_USERNAME>/strategy-harvester.git
git branch -M main
git push -u origin main
```

> Use a **private** repo — your committed DB contains your strategy cards and
> signal history. (Secrets themselves are never committed; they live in GitHub
> Secrets, see below.)

---

## 2. Add your secrets

In the GitHub web UI: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these (names must match exactly):

| Secret name           | Required? | What it is                                         |
|-----------------------|-----------|----------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`  | ✅ yes    | Bot token from @BotFather                           |
| `TELEGRAM_CHAT_ID`    | ✅ yes    | **Your** chat id (from `getUpdates`, not the bot's) |
| `BINANCE_API_KEY`     | ⬜ opt    | Binance key — public OHLCV works without it         |
| `BINANCE_API_SECRET`  | ⬜ opt    | Binance secret                                      |
| `CLAUDE_API_KEY`      | ⬜ opt    | Only used for offline extraction/adaptation         |

The workflow passes these into `runner_cron.py` as environment variables; the
runner reads them from the process environment (no `.env` file needed on CI).
Anything left unset degrades gracefully — the scan still runs.

> ⚠️ The `TELEGRAM_CHAT_ID` must be **your personal chat id**, not the bot's id.
> Message your bot first, then open
> `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id":...}`.

---

## 3. Verify the first run

1. Push to GitHub (step 1). The workflow file `.github/workflows/scanner.yml`
   ships in the repo, so Actions picks it up automatically.
2. Go to the **Actions** tab → **StrategyHarvester Scanner**.
3. Click **Run workflow** (the `workflow_dispatch` trigger) to fire it manually
   instead of waiting for the top of the hour.
4. Watch the **Run one scan cycle** step — you should see the scan box and
   `✅ [Cron] Scan cycle complete ...`. If any signals fired, you'll get a
   Telegram alert.
5. Check the **Commit updated state back to repo** step — it either commits a
   `chore(cron): update state ...` commit or prints `No state changes to commit`.
6. After that, the hourly `schedule` trigger takes over automatically.

### Notes / troubleshooting

- **Schedule lag:** GitHub's cron is best-effort and can run several minutes
  late, especially on the hour. This is normal — don't tighten the cron to
  compensate.
- **Schedules pause on inactive repos:** if there are no commits for ~60 days,
  GitHub disables scheduled workflows. The hourly DB commit keeps the repo
  active, so this won't trigger in practice.
- **Timeout:** the job is capped at 10 minutes (`timeout-minutes: 10`). A normal
  50-coin scan takes ~2 minutes locally.
- **Concurrency:** runs won't overlap (`concurrency: scanner`), so two cron
  ticks can't race on the DB.
- **Want a different cadence?** Edit the `cron:` line in
  `.github/workflows/scanner.yml` (e.g. `"*/30 * * * *"` for every 30 min).
