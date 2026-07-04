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
                "COALESCE(mode,'live') AS mode, date_generated "
                "FROM signals ORDER BY date_generated DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def _short_name(strategy_name: str) -> str:
    """Compact label for a strategy ('CRT …' → 'CRT', 'Volume Profile FRVP' → 'FRVP')."""
    n = (strategy_name or "").upper()
    for tag in ("CRT", "FRVP", "RANGE", "TEXTBOOK", "CISD"):
        if tag in n:
            return tag
    # else first word of the original (preserve case)
    return (strategy_name or "?").split(" (")[0].split()[0]


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


def _per_strategy(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scoreboard rows aggregated per strategy (across all timeframes/modes)."""
    groups: dict[str, list] = {}
    for s in signals:
        groups.setdefault(_short_name(s.get("strategy_name", "?")), []).append(s)
    out = []
    for name, sigs in groups.items():
        perf = _performance(sigs)
        perf["strategy"] = name
        out.append(perf)
    out.sort(key=lambda p: -p["total"])
    return out


def build_stats() -> dict[str, Any]:
    """Aggregate all dashboard data from the DB + config."""
    scan = db.get_scan_run_stats()
    signals = _signal_rows()
    live_signals = [s for s in signals if s.get("mode") != "shadow"]
    shadow_signals = [s for s in signals if s.get("mode") == "shadow"]
    coins = load_config().get("default_assets", [])
    healthy = (scan.get("last_status") or "ok") == "ok"

    # Per strategy+timeframe+mode breakdown, with a compact display label.
    breakdown = db.signal_breakdown()
    for b in breakdown:
        b["label"] = _short_name(b["strategy_name"])
    return {
        "generated_at": utc_now_str(),
        "scan": scan,
        "signals": signals,                               # full log (live+shadow)
        "live_signals": live_signals,
        "shadow_signals": shadow_signals,
        "overall": _performance(signals),                 # scoreboard = ALL
        "live_performance": _performance(live_signals),
        "shadow_performance": _performance(shadow_signals),
        "per_strategy": _per_strategy(signals),
        "breakdown": breakdown,
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


def _mode_badge(mode: Any) -> str:
    live = (mode or "live") != "shadow"
    return (f'<span class="badge {"live" if live else "shadow"}">'
            f'{"LIVE" if live else "shadow"}</span>')


def _signal_rows_html(signals: list[dict[str, Any]]) -> str:
    """The full signal log: date, coin, strategy, tf, mode, conf, outcome+%."""
    if not signals:
        return ('<tr><td colspan="7" class="muted" '
                'style="text-align:center;padding:24px">'
                'No signals recorded yet.</td></tr>')
    out = []
    for s in signals[:300]:  # cap rendered rows
        when = _esc(str(s.get("date_generated", ""))[:16])
        coin = _esc(str(s.get("asset", "")).split("/")[0])
        strat = _esc(_short_name(s.get("strategy_name", "?")))
        tf = _esc(s.get("timeframe", ""))
        conf = _esc(s.get("confidence_score", "—"))
        move = s.get("outcome_pct_move")
        move_str = f"{float(move):+.2f}%" if move is not None else ""
        out.append(
            f"<tr><td>{when}</td><td><b>{coin}</b></td><td>{strat}</td>"
            f"<td>{tf}</td><td>{_mode_badge(s.get('mode'))}</td><td>{conf}</td>"
            f"<td>{_outcome_badge(s.get('outcome_result'))} "
            f'<span class="muted">{move_str}</span></td></tr>')
    return "\n".join(out)


def _scoreboard_html(per_strategy: list[dict[str, Any]]) -> str:
    """Per-strategy running scoreboard rows."""
    if not per_strategy:
        return ('<tr><td colspan="6" class="muted" '
                'style="text-align:center;padding:20px">'
                'No signals yet.</td></tr>')
    out = []
    for p in per_strategy:
        pf = p["profit_factor"]
        pf_str = "∞" if pf >= 999 else f"{pf:.2f}"
        out.append(
            f"<tr><td><b>{_esc(p['strategy'])}</b></td>"
            f"<td>{p['total']}</td>"
            f'<td class="win-txt">{p["wins"]}</td>'
            f'<td class="loss-txt">{p["losses"]}</td>'
            f'<td class="muted">{p["pending"]}</td>'
            f"<td><b>{p['win_rate']}%</b> "
            f'<span class="muted">PF {pf_str}</span></td></tr>')
    return "\n".join(out)


def _breakdown_html(breakdown: list[dict[str, Any]]) -> str:
    """Rows for the strategy×timeframe×mode performance table."""
    if not breakdown:
        return ('<tr><td colspan="6" class="muted" '
                'style="text-align:center;padding:20px">'
                'No signals logged yet.</td></tr>')
    out = []
    for b in breakdown:
        live = b["mode"] == "live"
        tag = ('<span class="badge live">LIVE</span>' if live
               else '<span class="badge shadow">shadow</span>')
        pf = b["profit_factor"]
        pf_str = "∞" if pf >= 999 else f"{pf:.2f}"
        out.append(
            f"<tr><td><b>{_esc(b['label'])}</b> "
            f'<span class="muted">{_esc(b["timeframe"])}</span></td>'
            f"<td>{tag}</td>"
            f"<td>{b['signals']}</td>"
            f"<td>{b['win_rate']}%</td>"
            f"<td>{pf_str}</td>"
            f'<td class="muted">{b["wins"]}W/{b["losses"]}L/'
            f'{b["pending"]}P</td></tr>')
    return "\n".join(out)


def _coins_html(coins: list[str]) -> str:
    if not coins:
        return '<span class="muted">none configured</span>'
    return "".join(f'<span class="chip">{_esc(c)}</span>' for c in coins)


def render(stats: dict[str, Any]) -> str:
    """Render the full HTML page from aggregated stats."""
    scan = stats["scan"]
    ov = stats["overall"]          # scoreboard across ALL signals
    livep = stats["live_performance"]
    last_scan = scan.get("last_scan_at") or "never"
    health_txt = "Healthy ✅" if stats["healthy"] else "Degraded ⚠️"
    health_cls = "ok" if stats["healthy"] else "bad"
    ov_pf = "∞" if ov["profit_factor"] >= 999 else f"{ov['profit_factor']:.2f}"

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
  .badge.live {{ background:rgba(47,129,247,.18); color:var(--accent); }}
  .badge.shadow {{ background:var(--chip); color:var(--muted);
    border:1px solid var(--border); }}
  .muted {{ color:var(--muted); font-size:12px; }}
  .win-txt {{ color:var(--win); font-weight:700; }}
  .loss-txt {{ color:var(--loss); font-weight:700; }}
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
  <div class="muted" style="margin:-6px 0 8px">
    Live Telegram alerts fire from <b>CRT</b> (all timeframes; 🟢 4h proven,
    🟡 others unproven). FRVP/Range/Textbook/CISD are <b>shadow</b> — logged
    here, never alerted.</div>
  <div class="scroll">
  <table>
    <thead><tr><th>Strategy</th><th>Signals</th><th>W</th><th>L</th>
      <th>Pend</th><th>Win% / PF</th></tr></thead>
    <tbody>
      {_scoreboard_html(stats['per_strategy'])}
    </tbody>
  </table>
  </div>

  <h2>Performance by Strategy &amp; Timeframe</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Strategy / TF</th><th>Mode</th><th>Signals</th>
      <th>Win%</th><th>PF</th><th>W/L/Pending</th></tr></thead>
    <tbody>
      {_breakdown_html(stats['breakdown'])}
    </tbody>
  </table>
  </div>

  <h2>Full Signal Log (live + shadow)</h2>
  <div class="muted" style="margin:-4px 0 8px">
    {ov['total']} signals · newest first · every signal that fires, alerted or
    not.</div>
  <div class="scroll">
  <table>
    <thead><tr><th>Date (UTC)</th><th>Coin</th><th>Strategy</th><th>TF</th>
      <th>Mode</th><th>Conf</th><th>Outcome</th></tr></thead>
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
          f"({stats['overall']['total']} signals, "
          f"{stats['overall']['wins']}W/{stats['overall']['losses']}L, "
          f"{stats['scan'].get('scans_total',0)} scans).")
    return target
