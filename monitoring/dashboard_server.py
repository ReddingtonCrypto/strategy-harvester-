"""
Live, interactive dashboard server (Phase 3).

Unlike monitoring/dashboard.py (which renders a static HTML snapshot after
each scan, for GitHub Pages), this is a small live FastAPI app meant to run
continuously on the VM: same visual stats, computed fresh on every request,
plus a management UI — add/remove watchlist sources, see which strategies
are currently live/shadow/not-running, see recent content-intelligence
activity, and trigger an immediate run of any of the three scheduled jobs.

Gated behind a simple passphrase (config: DASHBOARD_PASSPHRASE in .env) —
this app can mutate state (add/remove sources, spawn processes), unlike the
old read-only static dashboard, so it needs *some* protection. No real user
system, no database of accounts — one shared passphrase, one signed session
cookie. That's deliberately as simple as this needs to be; don't add a real
auth framework here.

Run with:
    uvicorn monitoring.dashboard_server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import hashlib
import hmac
import html
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOKIE_NAME = "sh_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days

app = FastAPI(title="StrategyHarvester Dashboard")


@app.on_event("startup")
def _on_startup() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    from storage import strategy_store

    strategy_store.init()


# --- Auth (simple shared-passphrase + signed cookie) ----------------------

def _passphrase() -> str:
    from utils.helpers import get_env

    return get_env("DASHBOARD_PASSPHRASE") or ""


def _sign(value: str, key: bytes) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def _make_token(passphrase: str) -> str:
    key = hashlib.sha256(passphrase.encode()).digest()
    ts = str(int(time.time()))
    return f"{ts}.{_sign(ts, key)}"


def _token_valid(token: str, passphrase: str) -> bool:
    if not token or "." not in token:
        return False
    ts, _, sig = token.partition(".")
    key = hashlib.sha256(passphrase.encode()).digest()
    if not hmac.compare_digest(_sign(ts, key), sig):
        return False
    try:
        return (time.time() - int(ts)) < SESSION_MAX_AGE
    except ValueError:
        return False


def _is_authed(request: Request) -> bool:
    passphrase = _passphrase()
    if not passphrase:
        # Fail CLOSED: no passphrase configured means nobody gets in, rather
        # than silently having no protection at all.
        return False
    token = request.cookies.get(COOKIE_NAME, "")
    return _token_valid(token, passphrase)


# --- Data gathering (reuses the existing static-dashboard aggregations) ---

def _strategy_status_rows() -> list[dict[str, Any]]:
    from signals.market_scanner import _scan_strategies
    from storage import strategy_store
    from utils.helpers import load_config

    config = load_config()
    shadow_ids = set(config.get("shadow_strategies", []))
    observation_ids = set(config.get("observation_mode_strategies", []))
    running_ids = {c.id for c in _scan_strategies()}

    rows = []
    for c in strategy_store.list_cards():
        verdict = c.backtest_result.get("verdict") if isinstance(
            c.backtest_result, dict) else None
        if c.id not in running_ids:
            mode = "not running"
        elif c.id in shadow_ids:
            mode = "shadow"
        elif c.id in observation_ids:
            mode = "observation"
        else:
            mode = "live"
        rows.append({
            "id": c.id, "name": c.name, "version": c.version,
            "status": c.status, "approved": c.approved,
            "verdict": verdict or "—", "mode": mode,
            "source_type": c.source_type,
        })
    return rows


def _recent_content_cards(limit: int = 20) -> list[dict[str, Any]]:
    from storage import strategy_store

    content_types = {"youtube", "telegram", "twitter"}
    cards = [c for c in strategy_store.list_cards() if c.source_type in content_types]
    return [{
        "name": c.name, "source_type": c.source_type, "source_url": c.source_url,
        "confidence": c.confidence_score, "date_added": c.date_added,
        "status": c.status,
    } for c in cards[:limit]]


# --- HTML rendering ---------------------------------------------------------

def _esc(value: Any) -> str:
    return html.escape(str(value))


_BASE_CSS = """
  :root { --bg:#0d1117; --card:#161b22; --border:#30363d; --txt:#e6edf3;
    --muted:#8b949e; --accent:#2f81f7; --win:#3fb950; --loss:#f85149;
    --pending:#8b949e; --chip:#21262d; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,
    Arial,sans-serif; padding:16px; max-width:1000px; margin:0 auto; }
  h1 { font-size:20px; margin:4px 0 2px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:12px; margin-bottom:20px; }
  .card { background:var(--card); border:1px solid var(--border);
    border-radius:10px; padding:14px 16px; }
  .card .label { color:var(--muted); font-size:12px; text-transform:uppercase;
    letter-spacing:.04em; }
  .card .value { font-size:24px; font-weight:700; margin-top:6px; }
  .value.ok { color:var(--win); } .value.bad { color:var(--loss); }
  h2 { font-size:15px; margin:22px 0 10px; color:var(--muted);
    text-transform:uppercase; letter-spacing:.05em; }
  table { width:100%; border-collapse:collapse; font-size:13px;
    background:var(--card); border:1px solid var(--border); border-radius:10px;
    overflow:hidden; }
  th,td { padding:9px 10px; text-align:left; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:600; font-size:11px;
    text-transform:uppercase; }
  tr:last-child td { border-bottom:none; }
  .badge { display:inline-block; min-width:20px; text-align:center;
    padding:1px 7px; border-radius:20px; font-size:11px; font-weight:700; }
  .badge.win { background:rgba(63,185,80,.15); color:var(--win); }
  .badge.loss { background:rgba(248,81,73,.15); color:var(--loss); }
  .badge.live { background:rgba(47,129,247,.18); color:var(--accent); }
  .badge.shadow { background:var(--chip); color:var(--muted); border:1px solid var(--border); }
  .badge.observation { background:rgba(219,171,52,.18); color:#dbab34; }
  .badge.notrunning { background:var(--chip); color:var(--muted); }
  .muted { color:var(--muted); font-size:12px; }
  .win-txt { color:var(--win); font-weight:700; }
  .loss-txt { color:var(--loss); font-weight:700; }
  .chip { display:inline-block; background:var(--chip); border:1px solid var(--border);
    border-radius:6px; padding:3px 8px; margin:3px 3px 0 0; font-size:12px; }
  .scroll { overflow-x:auto; }
  footer { color:var(--muted); font-size:12px; margin:24px 0 8px; text-align:center; }
  input, button, select { font-family:inherit; font-size:13px; }
  input[type=text], input[type=password] { background:#0d1117; color:var(--txt);
    border:1px solid var(--border); border-radius:6px; padding:8px 10px; }
  button { background:var(--accent); color:#fff; border:none; border-radius:6px;
    padding:8px 14px; cursor:pointer; font-weight:600; }
  button:hover { opacity:.9; }
  .btn-danger { background:var(--loss); padding:5px 10px; font-size:12px; }
  .btn-secondary { background:var(--chip); border:1px solid var(--border); }
  form.inline { display:inline; }
  .toolbar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }
  .add-form { display:flex; gap:8px; flex-wrap:wrap; align-items:center;
    margin-bottom:14px; }
  .add-form select, .add-form input[type=text] { flex:1; min-width:140px; }
  a.logout { color:var(--muted); font-size:12px; text-decoration:none; float:right; }
"""


def _login_page(error: str = "") -> str:
    err_html = (f'<div class="muted" style="color:var(--loss);margin-bottom:10px">'
                f'{_esc(error)}</div>' if error else "")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StrategyHarvester · Login</title>
<style>{_BASE_CSS}
  body {{ max-width:360px; margin-top:80px; }}
  .card {{ padding:24px; }}
</style></head><body>
  <div class="card">
    <h1>📡 StrategyHarvester</h1>
    <div class="sub">Enter the dashboard passphrase to continue.</div>
    {err_html}
    <form method="post" action="/login">
      <input type="password" name="passphrase" placeholder="Passphrase"
        style="width:100%;margin-bottom:10px" autofocus>
      <button type="submit" style="width:100%">Log in</button>
    </form>
  </div>
</body></html>"""


def _strategy_rows_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ('<tr><td colspan="6" class="muted" '
                'style="text-align:center;padding:20px">No strategies yet.</td></tr>')
    mode_class = {"live": "live", "shadow": "shadow", "observation": "observation",
                  "not running": "notrunning"}
    out = []
    for r in rows:
        cls = mode_class.get(r["mode"], "notrunning")
        out.append(
            f"<tr><td><b>{_esc(r['name'])}</b> <span class=\"muted\">v{r['version']}</span></td>"
            f"<td>{_esc(r['source_type'])}</td>"
            f"<td>{_esc(r['status'])}</td>"
            f"<td>{_esc(r['verdict'])}</td>"
            f'<td><span class="badge {cls}">{_esc(r["mode"])}</span></td>'
            f"<td class=\"muted\">{r['id']}</td></tr>")
    return "\n".join(out)


def _sources_rows_html(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return ('<tr><td colspan="6" class="muted" '
                'style="text-align:center;padding:20px">'
                'No sources yet — add one below.</td></tr>')
    out = []
    for s in sources:
        checked = s["last_checked_at"] or "never"
        item = s["last_item_id"] or "-"
        out.append(
            f"<tr><td>{_esc(s['source_type'])}</td>"
            f"<td>{_esc(s['identifier'])}</td>"
            f"<td>{_esc(s['label'] or '-')}</td>"
            f"<td class=\"muted\">{_esc(checked)}</td>"
            f"<td class=\"muted\">{_esc(item)}</td>"
            f'<td><form class="inline" method="post" '
            f'action="/sources/{s["id"]}/delete" '
            f"onsubmit=\"return confirm('Remove this source?')\">"
            f'<button type="submit" class="btn-danger">Remove</button>'
            f"</form></td></tr>")
    return "\n".join(out)


def _recent_content_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ('<tr><td colspan="5" class="muted" '
                'style="text-align:center;padding:20px">'
                'No content-intelligence strategies extracted yet.</td></tr>')
    out = []
    for r in rows:
        out.append(
            f"<tr><td><b>{_esc(r['name'])}</b></td>"
            f"<td>{_esc(r['source_type'])}</td>"
            f"<td class=\"muted\">{_esc(r['source_url'])}</td>"
            f"<td>{r['confidence']}</td>"
            f"<td class=\"muted\">{_esc(r['date_added'])}</td></tr>")
    return "\n".join(out)


def _dashboard_page() -> str:
    from monitoring import dashboard as static_dashboard
    from storage import database as db

    stats = static_dashboard.build_stats()
    sources = db.list_sources()
    strategy_rows = _strategy_status_rows()
    recent = _recent_content_cards()

    scan = stats["scan"]
    ov = stats["overall"]
    last_scan = scan.get("last_scan_at") or "never"
    health_txt = "Healthy ✅" if stats["healthy"] else "Degraded ⚠️"
    health_cls = "ok" if stats["healthy"] else "bad"
    ov_pf = "∞" if ov["profit_factor"] >= 999 else f"{ov['profit_factor']:.2f}"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>StrategyHarvester · Dashboard</title>
<style>{_BASE_CSS}</style></head><body>
  <a class="logout" href="/logout">Log out</a>
  <h1>📡 StrategyHarvester Dashboard</h1>
  <div class="sub">Last scan: <b>{_esc(last_scan)}</b> UTC ·
    data source: <b>{_esc(stats['data_source'])}</b> · live view, auto-refresh 2 min</div>

  <div class="grid">
    <div class="card"><div class="label">System</div>
      <div class="value {health_cls}">{health_txt}</div></div>
    <div class="card"><div class="label">Scans (today)</div>
      <div class="value">{scan.get('scans_today', 0)}</div>
      <div class="muted">{scan.get('scans_total', 0)} all-time</div></div>
    <div class="card"><div class="label">Win rate</div>
      <div class="value">{ov['win_rate']}%</div>
      <div class="muted">PF {ov_pf} · {ov['decided']} decided</div></div>
    <div class="card"><div class="label">Coins watched</div>
      <div class="value">{len(stats['coins'])}</div></div>
  </div>

  <h2>Run now</h2>
  <div class="toolbar">
    <form class="inline" method="post" action="/run/scan">
      <button type="submit">▶ Price scan</button></form>
    <form class="inline" method="post" action="/run/content-intel">
      <button type="submit" class="btn-secondary">▶ Content intelligence</button></form>
    <form class="inline" method="post" action="/run/adaptation">
      <button type="submit" class="btn-secondary">▶ Daily adaptation</button></form>
  </div>
  <div class="muted" style="margin-top:-8px">
    Runs in the background — refresh in a bit to see results. A price scan
    takes about 1-2 min; content-intelligence can take longer depending on
    watchlist size.</div>

  <h2>Strategies</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Strategy</th><th>Source</th><th>Status</th>
      <th>Backtest</th><th>Scan mode</th><th>ID</th></tr></thead>
    <tbody>{_strategy_rows_html(strategy_rows)}</tbody>
  </table>
  </div>

  <h2>Content watchlist</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Type</th><th>Identifier</th><th>Label</th>
      <th>Last checked</th><th>Checkpoint</th><th></th></tr></thead>
    <tbody>{_sources_rows_html(sources)}</tbody>
  </table>
  </div>
  <form class="add-form" method="post" action="/sources">
    <select name="source_type">
      <option value="youtube">YouTube</option>
      <option value="telegram">Telegram</option>
      <option value="twitter">X / Twitter</option>
    </select>
    <input type="text" name="identifier" placeholder="Channel URL / @handle" required>
    <input type="text" name="label" placeholder="Label (optional)">
    <button type="submit">Add source</button>
  </form>

  <h2>Recent content-intelligence activity</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Strategy</th><th>Source</th><th>From</th>
      <th>Confidence</th><th>Added</th></tr></thead>
    <tbody>{_recent_content_html(recent)}</tbody>
  </table>
  </div>

  <footer>StrategyHarvester · live dashboard, data pulled fresh on every load</footer>
</body></html>"""


# --- Routes -----------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(error: str = "") -> str:
    errors = {
        "wrong": "Incorrect passphrase.",
        "not_configured": "DASHBOARD_PASSPHRASE is not set in .env — "
                          "the dashboard is locked until it is.",
    }
    return _login_page(errors.get(error, ""))


@app.post("/login")
def login_submit(passphrase: str = Form(...)):
    configured = _passphrase()
    if not configured:
        return RedirectResponse("/login?error=not_configured", status_code=303)
    if not hmac.compare_digest(passphrase, configured):
        return RedirectResponse("/login?error=wrong", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, _make_token(configured), max_age=SESSION_MAX_AGE,
                    httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return _dashboard_page()


@app.post("/sources")
def add_source_route(request: Request, source_type: str = Form(...),
                     identifier: str = Form(...), label: str = Form("")):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    if source_type not in ("youtube", "telegram", "twitter"):
        return RedirectResponse("/", status_code=303)
    from storage import database as db
    from utils.helpers import today_str

    db.add_source(source_type, identifier.strip(), label.strip(), today_str())
    return RedirectResponse("/", status_code=303)


@app.post("/sources/{source_id}/delete")
def delete_source_route(request: Request, source_id: int):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    from storage import database as db

    db.delete_source(source_id)
    return RedirectResponse("/", status_code=303)


_RUN_MODULES = {
    "scan": "scheduler.runner_cron",
    "content-intel": "scheduler.content_intelligence_cron",
    "adaptation": "scheduler.adaptation_cron",
}


@app.post("/run/{job}")
def trigger_run(request: Request, job: str):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    module = _RUN_MODULES.get(job)
    if module:
        # Detached background process — same entry point the systemd timers
        # use, just started on demand. No systemd/sudo needed: this process
        # already runs as the same user with the same venv/env vars.
        subprocess.Popen([sys.executable, "-m", module], cwd=str(PROJECT_ROOT))
    return RedirectResponse("/", status_code=303)
