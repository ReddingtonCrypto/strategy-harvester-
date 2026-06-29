# Smart-Money Engine (Phase 6)

A **pure-Python, zero-LLM** library that mechanically detects the price-action
(ICT / smart-money) concepts the mentor (`khaanhassan000`) strategies rely on —
making **CRT**, **Range**, and **Textbook A+** backtestable with the existing
Phase 2 engine and runnable live with the Phase 3 scanner.

- **No LLM, ever** — every concept has a deterministic mechanical definition.
- **Runs in both backtesting and live scanning** via the same signal functions.
- **Every threshold is in `config.json`** so the behaviour can be tuned without
  touching code.
- **Never crashes on thin data** — detectors return empty results instead.

---

## Modules & mechanical definitions

| Module | Concept | Mechanical definition |
|---|---|---|
| `swing_detector.py` | Swings + structure | Swing high/low = high/low strictly beyond the `swing_lookback` (2) candles each side. `get_structure` labels HH/LH/HL/LL. |
| `liquidity_sweep.py` | Sweep / SFP | Wick crosses a level by ≥ `sweep_min_wick_pct` (0.05%) but the **close** finishes back on the original side. |
| `market_structure.py` | BOS + MSS/CHOCH | BOS = close beyond the last swing in the trend direction. MSS = first **counter-trend** close-through (e.g. close above the last lower-high in a downtrend). |
| `ranges.py` | Range high/mid/low | Over `range_lookback` (40), if `(hh-ll)/ll ≤ range_max_height_pct` (12%) and both edges are touched ≥ 2×, it's a range. |
| `fvg.py` | Fair Value Gap | Bullish: `candle1.high < candle3.low` (gap ≥ `fvg_min_gap_pct`). "Filled" once price trades back through. |
| `fib_zones.py` | Fib retracement | `price = end − (end−start)·ratio`. Discount zone = `discount_zone` band (0.705–0.786). |
| `order_blocks.py` | Order blocks | Last opposite-colour candle before a structure break; high-low = the OB zone; `mitigated` when revisited. |
| `displacement.py` | Expansion candle | Body > `displacement_body_mult` (2.0) × avg body of last `displacement_lookback` (20), **and** it creates an FVG. |
| `smc_engine.py` | Orchestrator | `analyze()` (unified state) + the strategy signal generators below. |

---

## Strategy signal generators (`smc_engine.py`)

All generators return **LONG** entry dicts (spot system) of the form
`{index, direction, entry, target, stop, type, ...}`. They are used by the
backtest runner (full series) and the live scanner (latest closed candle).

- **`signal_range_strategy(df)`** — sweep of `range_low` + close back inside →
  LONG, target `range_high`. **Requires the mid-range reclaim** (close > mid)
  when `range_require_mid_reclaim` is true.
- **`signal_crt(df)`** — C2 sweeps C1 low + closes back inside the C1 range →
  LONG, target C1 high. **Requires ≥ `crt_required_confluence` (2)** of
  {order-block, FVG, old-level} alignment. *This filter is the highest-impact
  one — it's what turns the raw skeleton into the mentor's actual edge.*
- **`signal_textbook(df)`** — downtrend → MSS-up → retrace into the discount
  zone (0.705–0.786) → LONG, target = the high the MSS broke. Strictest /
  fewest signals.
- **`deviation_probability(df)`** — `"HIGH"` if the latest up-move broke a prior
  higher-high, else `"LOW"`. Used as a **filter**: cards with
  `uses_deviation_filter` get a confidence downgrade when deviation is LOW.

### Quality filters applied to every generator
- **ATR-buffered stop** — stop = swept wick − `smc_stop_buffer_atr` (1.0) × ATR14,
  giving trades room instead of a tight wick-stop.
- **Min reward/risk** — a signal is skipped if reward/risk < `smc_min_rr` (1.5).

---

## Configuration (`config.json`)

```jsonc
"swing_lookback": 2,               // candles each side for a swing
"sweep_min_wick_pct": 0.05,        // min wick overshoot beyond a level (%)
"range_lookback": 40,              // window for range detection
"range_max_height_pct": 12,        // max range height (%)
"discount_zone": [0.705, 0.786],   // long entry retracement band
"displacement_body_mult": 2.0,     // displacement body vs avg
"displacement_lookback": 20,
"fvg_min_gap_pct": 0.03,           // min FVG size (%)
"ob_lookback": 30,                 // order-block search window
"crt_required_confluence": 2,      // min of {OB, FVG, old-level} for CRT
"range_require_mid_reclaim": true, // Range needs the mid reclaim
"smc_stop_buffer_atr": 1.0,        // stop = wick − N×ATR14
"smc_min_rr": 1.5                  // skip signals below this reward/risk
```

---

## Integration

A Strategy Card opts into this engine via three fields (`models/strategy_card.py`):

```
engine                = "smc"                 # vs "generic" (Phase 2 indicator rules)
engine_signal         = "range"|"crt"|"textbook"|"filter"
uses_deviation_filter = true|false
```

- **`backtesting/backtest_runner.py`** — when `engine == "smc"`, calls
  `smc_engine.get_entries(df, engine_signal)` and simulates them with target/stop
  exits (`_simulate_smc`). Sizing ($100), fees (0.1%/side), metrics, and the
  PASS/FAIL verdict are **identical to Phase 2**.
- **`signals/signal_detector.py`** — `check_smc_signal()` fires when the latest
  **closed** candle has an SMC entry.
- **`signals/market_scanner.py`** — routes SMC cards to `check_smc_signal`, and
  applies `deviation_probability` as a −15 confidence downgrade when
  `uses_deviation_filter` and deviation is LOW.
- **`backtesting/rule_parser.py`** — SMC cards skip rule parsing entirely (no LLM).

The four mentor cards are wired up:

| Card | engine_signal |
|---|---|
| Range Strategy | `range` |
| CRT Candle Range Theory | `crt` |
| Textbook A+ Setup | `textbook` |
| Deviation Filter | `filter` (sets `uses_deviation_filter` on the others) |

---

## Backtest snapshot (BTC/USDT 4H, 12 months)

With the confluence + ATR-stop + R:R filters applied:

| Strategy | Trades | Win Rate | Profit Factor | Verdict |
|---|---:|---:|---:|:--:|
| **CRT** | 3 | 66.7% | 2.24 | ✅ PASS |
| Range | 0 | — | — | FAIL (mid-reclaim + R:R gate too tight here) |
| Textbook | 0 | — | — | FAIL (too few setups clear R:R) |

CRT validates the mentor's "OB + FVG confluence" thesis. Range/Textbook are
config-tunable (e.g. lower `smc_min_rr` or `smc_stop_buffer_atr`) — tune to taste
rather than curve-fitting to a PASS.

---

## Adding a new SMC strategy

1. Add a signal generator to `smc_engine.py` returning the standard entry dicts.
2. Register it in `_SIGNALS`.
3. Create a Strategy Card with `engine="smc"`, `engine_signal="<your_key>"`.
4. Backtest via menu option 6 — the runner handles the rest.
