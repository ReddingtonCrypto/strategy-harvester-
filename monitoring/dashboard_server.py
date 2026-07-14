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

import base64
import hashlib
import hmac
import html
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
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
  .flash { padding:12px 14px; border-radius:8px; margin-bottom:14px; font-size:13px;
    border:1px solid; }
  .flash.success { background:rgba(63,185,80,.12); border-color:rgba(63,185,80,.4);
    color:var(--win); }
  .flash.error { background:rgba(248,81,73,.12); border-color:rgba(248,81,73,.4);
    color:var(--loss); }
  .flash.info { background:rgba(139,148,158,.12); border-color:var(--border);
    color:var(--txt); }
  details > summary { cursor:pointer; color:var(--muted); font-size:12px; padding:8px 2px; }
  .hint { color:var(--muted); font-size:11px; margin:-6px 0 8px; }
"""


def _login_page(error: str = "") -> str:
    err_html = f'<div class="flash error"><b>❌</b> {_esc(error)}</div>' if error else ""
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


def _flash_html(msg: str) -> str:
    """Render a one-line result/status banner from a 'kind:detail' query value.

    Used after any action that redirects back to `/` with feedback: adding a
    source, processing a single video/message, etc.
    """
    if not msg:
        return ""
    kind, _, detail = msg.partition(":")
    icon = {"success": "✅", "error": "❌", "info": "ℹ️"}.get(kind, "ℹ️")
    cls = kind if kind in ("success", "error", "info") else "info"
    return f'<div class="flash {cls}"><b>{icon}</b> {_esc(detail)}</div>'


def _dashboard_page(msg: str = "") -> str:
    from ingestion import media_reader
    from monitoring import dashboard as static_dashboard
    from storage import database as db

    media_reader_formats = media_reader.VIDEO_FORMATS | media_reader.AUDIO_FORMATS
    stats = static_dashboard.build_stats()
    sources = db.list_sources()
    strategy_rows = _strategy_status_rows()
    recent = _recent_content_cards()
    flash_html = _flash_html(msg)

    scan = stats["scan"]
    ov = stats["overall"]
    last_scan = scan.get("last_scan_at") or "never"
    health_txt = "Healthy ✅" if stats["healthy"] else "Degraded ⚠️"
    health_cls = "ok" if stats["healthy"] else "bad"
    ov_pf = "∞" if ov["profit_factor"] >= 999 else f"{ov['profit_factor']:.2f}"

    signal_log_head = 12
    signals_all = stats["signals"]
    signal_rows_head = static_dashboard._signal_rows_html(signals_all[:signal_log_head])
    older_signals = signals_all[signal_log_head:]
    older_signals_html = ""
    if older_signals:
        older_signals_html = f"""
  <details style="margin-top:8px">
    <summary>Show {len(older_signals)} earlier signal(s)</summary>
    <div class="scroll" style="margin-top:8px">
    <table>
      <thead><tr><th>Date (UTC)</th><th>Coin</th><th>Strategy</th><th>TF</th>
        <th>Mode</th><th>Conf</th><th>Outcome</th></tr></thead>
      <tbody>{static_dashboard._signal_rows_html(older_signals)}</tbody>
    </table>
    </div>
  </details>"""

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
  {flash_html}

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

  <h2>Scoreboard (all signals)</h2>
  <div class="grid">
    <div class="card"><div class="label">Total signals</div>
      <div class="value">{ov['total']}</div>
      <div class="muted">{ov['pending']} pending</div></div>
    <div class="card"><div class="label">Wins</div>
      <div class="value ok">{ov['wins']}</div></div>
    <div class="card"><div class="label">Losses</div>
      <div class="value bad">{ov['losses']}</div></div>
    <div class="card"><div class="label">Win rate</div>
      <div class="value">{ov['win_rate']}%</div>
      <div class="muted">PF {ov_pf} · {ov['decided']} decided</div></div>
  </div>
  <div class="scroll">
  <table>
    <thead><tr><th>Strategy</th><th>Signals</th><th>W</th><th>L</th>
      <th>Pend</th><th>Win% / PF</th></tr></thead>
    <tbody>{static_dashboard._scoreboard_html(stats['per_strategy'])}</tbody>
  </table>
  </div>

  <h2>Performance by Strategy &amp; Timeframe</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Strategy / TF</th><th>Mode</th><th>Signals</th>
      <th>Win%</th><th>PF</th><th>W/L/Pending</th></tr></thead>
    <tbody>{static_dashboard._breakdown_html(stats['breakdown'])}</tbody>
  </table>
  </div>

  <h2>Full Signal Log (live + shadow)</h2>
  <div class="muted" style="margin:-4px 0 8px">
    {ov['total']} signals · newest first · showing latest {min(signal_log_head, ov['total'])}
    below, older ones collapsed.</div>
  <div class="scroll">
  <table>
    <thead><tr><th>Date (UTC)</th><th>Coin</th><th>Strategy</th><th>TF</th>
      <th>Mode</th><th>Conf</th><th>Outcome</th></tr></thead>
    <tbody>{signal_rows_head}</tbody>
  </table>
  </div>
  {older_signals_html}

  <h2>Coins Currently Watched</h2>
  <div>{static_dashboard._coins_html(stats['coins'])}</div>

  <h2>Paste a YouTube transcript</h2>
  <div class="muted" style="margin:-4px 0 8px">
    Automatic fetching (transcript API + audio download) is blocked from
    this VM's IP range by YouTube (confirmed — not fixable by changing the
    IP, it's Oracle's whole cloud range, not this one address). Workaround:
    open the video yourself, click "···" under the player → <b>Show
    transcript</b>, select all, copy, and paste it here instead — this
    never touches YouTube from the server at all.</div>
  <form class="add-form" method="post" action="/process-transcript" style="flex-direction:column">
    <input type="text" name="video_url" placeholder="Video URL (optional, for reference)"
      style="width:100%;margin-bottom:8px">
    <textarea name="transcript_text" placeholder="Paste the transcript text here..."
      style="width:100%;min-height:120px;background:#0d1117;color:var(--txt);
      border:1px solid var(--border);border-radius:6px;padding:8px 10px;
      margin-bottom:8px" required></textarea>
    <button type="submit">Extract now</button>
  </form>
  <div class="hint">Uses Claude Opus 4.8 — can take up to a minute. The button
    will say "Working…" and the page will reload with a result (green =
    success, red = error) when it's done.</div>

  <h2>Paste text or notes</h2>
  <div class="muted" style="margin:-4px 0 8px">
    Any free-form text — an article, a post, your own notes — extracted the
    same way as everything else.</div>
  <form class="add-form" method="post" action="/process-text" style="flex-direction:column">
    <input type="text" name="label" placeholder="Label (optional, e.g. a source name)"
      style="width:100%;margin-bottom:8px">
    <textarea name="content" placeholder="Paste text here..."
      style="width:100%;min-height:120px;background:#0d1117;color:var(--txt);
      border:1px solid var(--border);border-radius:6px;padding:8px 10px;
      margin-bottom:8px" required></textarea>
    <button type="submit">Extract now</button>
  </form>
  <div class="hint">Uses Claude Opus 4.8 — can take up to a minute. The button
    will say "Working…" and the page will reload with a result when it's done.</div>

  <h2>Upload an image (chart or post screenshot)</h2>
  <div class="muted" style="margin:-4px 0 8px">
    Sent to Claude Opus 4.8 vision for reading — works the same as menu
    option 17 locally, just from the browser.</div>
  <form class="add-form" method="post" action="/process-image"
    enctype="multipart/form-data" style="flex-direction:column">
    <input type="file" name="image_file" accept="image/png,image/jpeg,image/webp,image/gif"
      style="width:100%;margin-bottom:8px" required>
    <input type="text" name="notes" placeholder="Notes about the image (optional)"
      style="width:100%;margin-bottom:8px">
    <button type="submit">Extract now</button>
  </form>
  <div class="hint">Can take up to a minute. The button will say "Working…"
    and the page will reload with a result when it's done.</div>

  <h2>Upload a video or audio recording</h2>
  <div class="muted" style="margin:-4px 0 8px">
    Transcribed locally on the VM with Whisper (free, offline — no upload to
    YouTube/anyone), then extracted with Claude Opus 4.8. Supported:
    {', '.join(sorted(media_reader_formats))}. This VM is small — keep clips
    short (a few minutes) or expect a long wait; a long/large file can take
    several minutes to transcribe.</div>
  <form class="add-form" method="post" action="/process-media"
    enctype="multipart/form-data" style="flex-direction:column">
    <input type="file" name="media_file"
      accept="video/mp4,video/quicktime,video/x-matroska,video/x-msvideo,video/webm,
        audio/mpeg,audio/wav,audio/mp4,audio/aac"
      style="width:100%;margin-bottom:8px" required>
    <input type="text" name="notes" placeholder="Notes about the video (optional)"
      style="width:100%;margin-bottom:8px">
    <button type="submit">Extract now</button>
  </form>
  <div class="hint">Can take several minutes for longer files. The button
    will say "Working…" — please don't close the tab until the page reloads
    with a result.</div>

  <h2>Process one Telegram message right now</h2>
  <div class="muted" style="margin:-4px 0 8px">
    For a single message you found (not an ongoing channel) — paste a public
    message link (right-click a message → Copy Message Link in Telegram).
    Requires Telegram credentials + an authorized session (see
    SUMMARY_PHASE1.md); gracefully tells you if that's not set up yet. Uses
    Claude Opus 4.8.</div>
  <form class="add-form" method="post" action="/process-telegram-message">
    <input type="text" name="message_url" placeholder="https://t.me/channelname/12345"
      style="flex:2" required>
    <button type="submit">Extract now</button>
  </form>

  <h2>Content watchlist</h2>
  <div class="muted" style="margin:-4px 0 8px">
    Ongoing channels/accounts — scanned immediately when added, then
    automatically every 4 hours after that. Use a <b>channel</b> URL (e.g.
    youtube.com/@name) or a Telegram <b>@channelname</b>, not a single
    video/message link.</div>
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
  <script>
    document.querySelectorAll("form").forEach(function (f) {{
      f.addEventListener("submit", function () {{
        var btn = f.querySelector("button[type=submit]");
        if (btn) {{ btn.disabled = true; btn.textContent = "Working…"; }}
      }});
    }});
  </script>
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
def index(request: Request, msg: str = ""):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return _dashboard_page(msg)


def _extract_and_save(text: str, *, source_type: str, source_url: str,
                      extraction_mode: Optional[str]) -> tuple[bool, str]:
    """Shared extract+save+message logic for the paste-based routes below.

    Always uses Opus 4.8 — this is manually-curated content the user is
    submitting directly, so it gets the higher-quality model rather than the
    fast Haiku tier the automated watchlist crons use.

    Returns (found, redirect_message) — message already has the "kind:"
    prefix _flash_html() expects.
    """
    import extraction.strategy_extractor as extractor

    card = extractor.extract_strategy(
        text, source_type=source_type, source_url=source_url,
        force_mode=extraction_mode, model=extractor.OPUS_MODEL)
    if card and card.confidence_score > 0:
        from storage import strategy_store

        strategy_store.save_card(card)
        return True, "success:" + quote("Strategy extracted: " + card.name)
    if extractor.LAST_ERROR:
        return False, "error:" + quote(extractor.LAST_ERROR)
    return False, "info:" + quote("No strategy found (or confidence was 0).")


@app.post("/process-transcript")
def process_transcript_route(request: Request, transcript_text: str = Form(...),
                             video_url: str = Form("")):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)

    from utils.helpers import clean_text, load_config

    text = clean_text(transcript_text)
    if not text:
        return RedirectResponse(
            f"/?msg=error:{quote('No transcript text provided.')}", status_code=303)

    extraction_mode = load_config().get("extraction_mode")
    _found, msg = _extract_and_save(
        text, source_type="youtube", source_url=video_url.strip() or "manual-paste",
        extraction_mode=extraction_mode)
    return RedirectResponse(f"/?msg={msg}", status_code=303)


@app.post("/process-text")
def process_text_route(request: Request, content: str = Form(...), label: str = Form("")):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)

    from utils.helpers import clean_text, load_config

    text = clean_text(content)
    if not text:
        return RedirectResponse(
            f"/?msg=error:{quote('No text provided.')}", status_code=303)

    extraction_mode = load_config().get("extraction_mode")
    _found, msg = _extract_and_save(
        text, source_type="manual", source_url=label.strip() or "dashboard_paste",
        extraction_mode=extraction_mode)
    return RedirectResponse(f"/?msg={msg}", status_code=303)


@app.post("/process-image")
async def process_image_route(request: Request, image_file: UploadFile = File(...),
                              notes: str = Form("")):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)

    from ingestion import image_reader

    data = await image_file.read()
    max_mb = 5.0
    try:
        from utils.helpers import load_config

        max_mb = float(load_config().get("image_max_size_mb", 5))
    except Exception:  # noqa: BLE001 — fall back to the default above
        pass
    if len(data) / (1024 * 1024) > max_mb:
        return RedirectResponse(
            f"/?msg=error:{quote(f'Image is over the {max_mb:.0f}MB limit.')}",
            status_code=303)

    ext = (image_file.filename or "").rsplit(".", 1)[-1].lower()
    media_type = image_reader._MEDIA_TYPES.get(ext, image_file.content_type or "image/png")
    b64 = base64.b64encode(data).decode("ascii")

    image_data = {
        "image_base64": b64, "media_type": media_type,
        "text_notes": notes.strip(), "source_type": "image_input",
        "source_label": f"image: {image_file.filename or 'upload'}",
    }
    parsed = image_reader.extract_from_image(image_data)
    if not parsed:
        detail = image_reader.LAST_ERROR or "Claude could not read the image."
        return RedirectResponse(f"/?msg=error:{quote(detail)}", status_code=303)
    card = image_reader.build_and_save_card(parsed, image_data)
    if card:
        success_text = "Strategy extracted: " + card.name
        return RedirectResponse(f"/?msg=success:{quote(success_text)}", status_code=303)
    return RedirectResponse(
        f"/?msg=info:{quote('No clear strategy found in that image.')}", status_code=303)


@app.post("/process-media")
async def process_media_route(request: Request, media_file: UploadFile = File(...),
                              notes: str = Form("")):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)

    import tempfile

    from ingestion import media_reader
    from utils.helpers import load_config

    ext = (media_file.filename or "").rsplit(".", 1)[-1].lower()
    supported = media_reader.VIDEO_FORMATS | media_reader.AUDIO_FORMATS
    if ext not in supported:
        return RedirectResponse(
            f"/?msg=error:{quote('Unsupported format .' + ext + '. Supported: ' + ', '.join(sorted(supported)))}",
            status_code=303)

    data = await media_file.read()
    max_mb = float(load_config().get("max_video_size_mb", 500))
    if len(data) / (1024 * 1024) > max_mb:
        return RedirectResponse(
            f"/?msg=error:{quote(f'File is over the {max_mb:.0f}MB limit.')}",
            status_code=303)

    tmp_dir = Path(tempfile.gettempdir()) / "strategy_harvester_media"
    tmp_dir.mkdir(exist_ok=True)
    tmp_path = tmp_dir / f"upload_{int(time.time() * 1000)}.{ext}"
    tmp_path.write_bytes(data)

    try:
        result = media_reader.read_local_media(str(tmp_path))
        if not result:
            detail = media_reader.LAST_ERROR or "Could not transcribe that file."
            return RedirectResponse(f"/?msg=error:{quote(detail)}", status_code=303)

        text = result["text"]
        note_text = notes.strip()
        if note_text:
            text = f"[USER NOTES]: {note_text}\n\n{text}"

        import extraction.strategy_extractor as extractor
        from storage import strategy_store

        card = extractor.extract_strategy(
            text, source_type=result["source_type"], source_url=result["source_label"],
            model=extractor.OPUS_MODEL)
        if card and card.confidence_score > 0:
            strategy_store.save_card(card)
            success_text = "Strategy extracted: " + card.name
            return RedirectResponse(f"/?msg=success:{quote(success_text)}", status_code=303)
        if extractor.LAST_ERROR:
            return RedirectResponse(
                f"/?msg=error:{quote(extractor.LAST_ERROR)}", status_code=303)
        return RedirectResponse(
            f"/?msg=info:{quote('No strategy found in that recording.')}", status_code=303)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/process-telegram-message")
def process_telegram_message_route(request: Request, message_url: str = Form(...)):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)

    from extraction.strategy_extractor import OPUS_MODEL
    from ingestion.telegram_reader import process_single_message
    from utils.helpers import load_config

    extraction_mode = load_config().get("extraction_mode")
    result = process_single_message(message_url.strip(), extraction_mode=extraction_mode,
                                    model=OPUS_MODEL)

    if result["error"]:
        return RedirectResponse(f"/?msg=error:{quote(result['error'])}", status_code=303)
    if result["strategy"]:
        text = "Strategy extracted: " + result["strategy"]
        return RedirectResponse(f"/?msg=success:{quote(text)}", status_code=303)
    return RedirectResponse(
        f"/?msg=info:{quote('No strategy found in that message (or confidence was 0).')}",
        status_code=303)


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
    # Don't make them wait up to 4 hours for the first check — kick off a
    # content-intelligence pass right away (background, same mechanism as
    # the "Run now" button). It re-checks every active source of every
    # type, not just the one just added, which is fine — that's the same
    # thing the scheduled timer does anyway.
    subprocess.Popen([sys.executable, "-m", "scheduler.content_intelligence_cron"],
                     cwd=str(PROJECT_ROOT))
    return RedirectResponse(
        f"/?msg=info:{quote('Source added — scanning now in the background.')}",
        status_code=303)


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
