"""
Static monitoring dashboard (GitHub Pages).

`build_stats()` aggregates everything from the existing DB — scan runs, signals,
outcomes — with no new data collection. `generate()` renders a self-contained,
dark-theme, mobile-friendly `dashboard/index.html` that auto-refreshes every
5 minutes. The GitHub Actions workflow commits that file each run; GitHub Pages
serves it.
"""

from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Any

from storage import database as db
from utils.helpers import load_config, utc_now_str

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# GitHub Pages "Deploy from a branch" only serves `/` or `/docs` — not an
# arbitrary folder — so the dashboard is written to docs/index.html.
DASHBOARD_DIR = PROJECT_ROOT / "docs"
REFRESH_SECONDS = 300  # auto-refresh meta (5 minutes)


# --- Data aggregation ----------------------------------------------------

def _signal_rows() -> list[dict[str, Any]]:
    """All signals, newest first, as plain dicts (empty on any error)."""
    try:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT strategy_name, asset, timeframe, entry_price_at_signal, "
                "confidence_score, outcome_result, outcome_pct_move, "
                "date_generated FROM signals ORDER BY date_generated DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def _performance(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Win rate + running profit factor from filled outcomes."""
    wins = [s for s in signals if s.get("outcome_result") == "WIN"]
    losses = [s for s in signals if s.get("outcome_result") == "LOSS"]
    decided = len(wins) + len(losses)
    pending = sum(1 for s in signals if not s.get("outcome_result")
                  or s.get("outcome_result") == "NEUTRAL")

    gross_win = sum(abs(float(s.get("outcome_pct_move") or 0)) for s in wins)
    gross_loss = sum(abs(float(s.get("outcome_pct_move") or 0)) for s in losses)
    if gross_loss > 0:
        pf = gross_win / gross_loss
    elif gross_win > 0:
        pf = 999.99
    else:
        pf = 0.0

    win_rate = (len(wins) / decided * 100) if decided else 0.0
    return {
        "total": len(signals), "wins": len(wins), "losses": len(losses),
        "pending": pending, "decided": decided,
        "win_rate": round(win_rate, 1), "profit_factor": round(pf, 2),
    }


def build_stats() -> dict[str, Any]:
    """Aggregate all dashboard data from the DB + config."""
    scan = db.get_scan_run_stats()
    signals = _signal_rows()
    perf = _performance(signals)
    coins = load_config().get("default_assets", [])
    healthy = (scan.get("last_status") or "ok") == "ok"
    return {
        "generated_at": utc_now_str(),
        "scan": scan,
        "signals": signals,
        "performance": perf,
        "coins": coins,
        "healthy": healthy,
        "data_source": scan.get("last_source") or "n/a",
    }


# --- Rendering -----------------------------------------------------------

def _esc(value: Any) -> str:
    return html.escape(str(value))


def _outcome_badge(result: Any) -> str:
    r = (result or "").upper()
    cls = {"WIN": "win", "LOSS": "loss"}.get(r, "pending")
    label = {"WIN": "W", "LOSS": "L", "NEUTRAL": "—"}.get(r, "pending")
    if r not in ("WIN", "LOSS", "NEUTRAL"):
        label = "pending"
    return f'<span class="badge {cls}">{label}</span>'


def _signal_rows_html(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return ('<tr><td colspan="6" class="muted" '
                'style="text-align:center;padding:24px">'
                'No signals recorded yet.</td></tr>')
    out = []
    for s in signals[:200]:  # cap rendered rows
        when = _esc(str(s.get("date_generated", ""))[:16])
        coin = _esc(str(s.get("asset", "")).split("/")[0])
        tf = _esc(s.get("timeframe", ""))
        entry = s.get("entry_price_at_signal")
        entry_str = f"{float(entry):,.4f}".rstrip("0").rstrip(".") if entry else "—"
        conf = _esc(s.get("confidence_score", "—"))
        move = s.get("outcome_pct_move")
        move_str = f"{float(move):+.2f}%" if move is not None else ""
        out.append(
            f"<tr><td>{when}</td><td><b>{coin}</b></td><td>{tf}</td>"
            f"<td>{entry_str}</td><td>{conf}</td>"
            f"<td>{_outcome_badge(s.get('outcome_result'))} "
            f'<span class="muted">{move_str}</span></td></tr>')
    return "\n".join(out)


def _coins_html(coins: list[str]) -> str:
    if not coins:
        return '<span class="muted">none configured</span>'
    return "".join(f'<span class="chip">{_esc(c)}</span>' for c in coins)


def render(stats: dict[str, Any]) -> str:
    """Render the full HTML page from aggregated stats."""
    scan = stats["scan"]
    perf = stats["performance"]
    last_scan = scan.get("last_scan_at") or "never"
    health_txt = "Healthy ✅" if stats["healthy"] else "Degraded ⚠️"
    health_cls = "ok" if stats["healthy"] else "bad"
    pf = perf["profit_factor"]
    pf_str = "∞" if pf >= 999 else f"{pf:.2f}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_SECONDS}">
<title>StrategyHarvester · Monitor</title>
<style>
  :root {{
    --bg:#0d1117; --card:#161b22; --border:#30363d; --txt:#e6edf3;
    --muted:#8b949e; --accent:#2f81f7; --win:#3fb950; --loss:#f85149;
    --pending:#8b949e; --chip:#21262d;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,
    Arial,sans-serif; padding:16px; max-width:960px; margin:0 auto; }}
  h1 {{ font-size:20px; margin:4px 0 2px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:16px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:12px; margin-bottom:20px; }}
  .card {{ background:var(--card); border:1px solid var(--border);
    border-radius:10px; padding:14px 16px; }}
  .card .label {{ color:var(--muted); font-size:12px; text-transform:uppercase;
    letter-spacing:.04em; }}
  .card .value {{ font-size:24px; font-weight:700; margin-top:6px; }}
  .value.ok {{ color:var(--win); }} .value.bad {{ color:var(--loss); }}
  h2 {{ font-size:15px; margin:22px 0 10px; color:var(--muted);
    text-transform:uppercase; letter-spacing:.05em; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px;
    background:var(--card); border:1px solid var(--border); border-radius:10px;
    overflow:hidden; }}
  th,td {{ padding:9px 10px; text-align:left; border-bottom:1px solid var(--border); }}
  th {{ color:var(--muted); font-weight:600; font-size:11px;
    text-transform:uppercase; }}
  tr:last-child td {{ border-bottom:none; }}
  .badge {{ display:inline-block; min-width:20px; text-align:center;
    padding:1px 7px; border-radius:20px; font-size:11px; font-weight:700; }}
  .badge.win {{ background:rgba(63,185,80,.15); color:var(--win); }}
  .badge.loss {{ background:rgba(248,81,73,.15); color:var(--loss); }}
  .badge.pending {{ background:var(--chip); color:var(--pending); }}
  .muted {{ color:var(--muted); font-size:12px; }}
  .chip {{ display:inline-block; background:var(--chip); border:1px solid var(--border);
    border-radius:6px; padding:3px 8px; margin:3px 3px 0 0; font-size:12px; }}
  .scroll {{ overflow-x:auto; }}
  footer {{ color:var(--muted); font-size:12px; margin:24px 0 8px; text-align:center; }}
</style>
</head>
<body>
  <h1>📡 StrategyHarvester Monitor</h1>
  <div class="sub">Last scan: <b>{_esc(last_scan)}</b> UTC ·
    data source: <b>{_esc(stats['data_source'])}</b> ·
    auto-refresh 5 min</div>

  <div class="grid">
    <div class="card"><div class="label">System</div>
      <div class="value {health_cls}">{health_txt}</div></div>
    <div class="card"><div class="label">Scans (today)</div>
      <div class="value">{scan.get('scans_today',0)}</div>
      <div class="muted">{scan.get('scans_total',0)} all-time</div></div>
    <div class="card"><div class="label">Signals (today)</div>
      <div class="value">{scan.get('signals_today',0)}</div>
      <div class="muted">{scan.get('signals_total',0)} all-time</div></div>
    <div class="card"><div class="label">Coins watched</div>
      <div class="value">{len(stats['coins'])}</div></div>
  </div>

  <h2>CRT Live Performance (observation mode)</h2>
  <div class="grid">
    <div class="card"><div class="label">Signals</div>
      <div class="value">{perf['total']}</div>
      <div class="muted">{perf['pending']} pending</div></div>
    <div class="card"><div class="label">Win rate</div>
      <div class="value">{perf['win_rate']}%</div>
      <div class="muted">{perf['wins']}W / {perf['losses']}L</div></div>
    <div class="card"><div class="label">Profit factor</div>
      <div class="value">{pf_str}</div>
      <div class="muted">{perf['decided']} decided</div></div>
  </div>

  <h2>Signal Log</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Date (UTC)</th><th>Coin</th><th>TF</th><th>Entry</th>
      <th>Conf</th><th>Outcome</th></tr></thead>
    <tbody>
      {_signal_rows_html(stats['signals'])}
    </tbody>
  </table>
  </div>

  <h2>Coins Currently Watched</h2>
  <div>{_coins_html(stats['coins'])}</div>

  <footer>Generated {_esc(stats['generated_at'])} UTC ·
    StrategyHarvester · data pulled from local DB</footer>
</body>
</html>"""


def generate(out_path: Path | None = None) -> Path:
    """Build stats, render HTML, and write dashboard/index.html. Returns the path."""
    stats = build_stats()
    target = out_path or (DASHBOARD_DIR / "index.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(stats), encoding="utf-8")
    print(f"📊 [Dashboard] Wrote {target} "
          f"({stats['performance']['total']} signals, "
          f"{stats['scan'].get('scans_total',0)} scans).")
    return target
