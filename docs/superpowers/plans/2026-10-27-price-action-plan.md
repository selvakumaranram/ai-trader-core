# Strategy D — Price Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth backtestable strategy, D — Price action (market structure, pullback-to-support and breakout-retest setups, half profit at 1.5R then breakeven + trailing stop), for Nifty 200 stocks and NIFTY/BANKNIFTY, plus a "two losses in a row" notice for every strategy.

**Architecture:** Structure features (swing points, trend, support/resistance, candles, breakout-retest, choppy flag) are precomputed per instrument by a causal `add_structure(df)`; strategy D reads one row per symbol per day like A–C. Exits become a per-strategy policy: A–C keep the fixed 2R target, D uses a new `partial_trail_check` state machine shared by the stock engine and the index walker. RBI policy days come from an editable YAML file and block entries engine-side.

**Tech Stack:** Python 3.12, pandas/numpy, SQLAlchemy/Postgres (no schema change), FastAPI; React 18 + lightweight-charts 5.2.1.

**Spec:** `docs/specs/2026-10-27-price-action-design.md` (builds on `docs/specs/2026-10-22-backtest-design.md`)

**Branches:** `strategy-d` in `D:\ai-trader-core` from `reports-and-history-check`; `strategy-d-tab` in `D:\ai-trader` from `report-download`.

## Global Constraints

- No decision at day *t* may use bars after *t*. A swing point at bar *i* is usable only when *i + 2 ≤ t*.
- A, B and C must behave exactly as before: every existing test passes unchanged.
- D defaults: risk per trade ₹300, daily loss stop ₹600; capital ₹20,000, 3 positions, ₹7,000 per position.
- D numbers (stored in `PARAMS`): swing 2 bars each side; levels from last 60 bars, cluster within 1%, ≥ 2 touches;
  pullback within 1% of support or EMA20; hammer lower wick ≥ 2× body, close in upper third, range > 0;
  stop distance 1–8%; breakout range 20 bars spanning ≤ 12%, volume ≥ 1.5× 20-day average (stocks only),
  retest within 5 bars, low within ±1% of range high, close above it; choppy = ATR(10)/close in lowest 20% of its
  previous 250 values; room to nearest level ≥ 2R; partial at 1.5R (half, rounded down; 1 share → all);
  breakeven from next day; trail = max(stop, lowest low of last 5 bars) after the partial; time stop 30 days;
  ranking by room in R capped at 5, then setup 2 before setup 1, then symbol.
- Same-day ambiguity is always resolved against the trade (stop first).
- RBI dates are never guessed; missing coverage produces a warning, never a crash.
- Copy never promises profit. No order placement anywhere.

## Review Focus

1. A stock with fewer than 250 bars at signal time — the choppy filter must pass (not crash, not block). → Task 1 `test_choppy_needs_250_bars`.
2. No qualifying resistance above the close — room-to-target passes and the room is treated as the 5R cap. → Task 2 `test_no_level_in_the_way_scores_five`.
3. A doji / zero-range day (high == low) — never a hammer, no division error. → Task 1 `test_zero_range_is_not_a_hammer`.
4. A one-share D position reaching 1.5R — the whole position closes, no zero-share remainder. → Task 4 `test_one_share_partial_closes_everything`.
5. `rbi_policy_dates.yaml` missing or empty — runs succeed with the "RBI dates missing" warning. → Task 5 `test_missing_rbi_file_warns`.

---

### Task 1: Structure features

**Files:**
- Create: `aitrader/backtest/structure.py`
- Test: `tests/test_bt_structure.py`

**Interfaces:**
- Produces:
  - `swing_points(high: pd.Series, low: pd.Series, n: int = 2) -> tuple[pd.Series, pd.Series]` — bool series *at bar i* (uses i±n; callers must shift).
  - `known_swings(df) -> pd.DataFrame` columns `sh1, sh2, sl1, sl2` (previous and latest swing high/low prices known at *t*, i.e. swing bar ≤ t − 2; NaN when not enough).
  - `trend(df) -> pd.Series` of `1` (up), `-1` (down), `0` (sideways) per spec §3.2, from `sh1, sh2, sl1, sl2, close`.
  - `levels_at(prices: Sequence[float], tolerance: float = 0.01, min_touches: int = 2) -> list[tuple[float, int]]` — qualifying (mean level, touches).
  - `hammer(df)`, `shooting_star(df)`, `bull_engulf(df)`, `bear_engulf(df)` -> bool series.
  - `add_structure(df: pd.DataFrame, use_volume: bool = True) -> pd.DataFrame` — input already has `add_indicators` columns; adds
    `ema20, sh1, sh2, sl1, sl2, trend, support, support_touches, resistance, resistance_touches, hammer, shooting_star,
    bull_engulf, bear_engulf, choppy, retest_long, retest_short, retest_level, low5, high5`
    (`support` = nearest qualifying level ≤ close, `resistance` = nearest ≥ close, NaN when none;
    `retest_level` = range high (long) or range low (short) being retested).

- [ ] **Step 1: Write the failing tests** (`tests/test_bt_structure.py`, frames via `bt_helpers.frame`)
  - `test_swing_high_is_unknown_until_two_bars_later`: highs `[1,2,5,2,1,1]` → `known_swings` row 3 `sh2` NaN, row 4 `sh2 == 5`.
  - `test_trend_up_down_sideways`: rising zig-zag → last `trend == 1`; falling mirror → `-1`; flat → `0`.
  - `test_levels_cluster_within_one_percent`: `levels_at([100, 100.5, 120, 130, 130.9])` ≈ `[(100.25, 2), (130.45, 2)]`.
  - `test_hammer_and_engulfing`: bar `(o=100,h=101,l=94,c=100.8)` hammer True; `(100,101,99,100.5)` False; pair red `(101,101.5,98.8,99)` then green `(98.5,101.8,98.4,101.5)` → `bull_engulf` True.
  - `test_zero_range_is_not_a_hammer`: `(100,100,100,100)` → hammer False, no warnings.
  - `test_choppy_needs_250_bars`: 200-bar frame → `choppy` all False.
  - `test_choppy_flags_quietest_fifth`: 300 bars, last 30 with a tenth of earlier ranges → last row `choppy` True.
  - `test_breakout_retest_long`: 20 flat bars (high 101), breakout bar close 103 volume 3×, then bar low 101.2 close 102.5 → `retest_long` True, `retest_level == 101`; a later second retest → False.
  - `test_breakout_requires_first_close_above`: two consecutive closes above the range high → one breakout, one window.
  - `test_retest_ignores_volume_when_disabled`: volume 0 and `use_volume=False` → `retest_long` True.
  - `test_add_structure_is_causal`: mutate all bars after index 260 of a random walk → every column up to 260 identical.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_bt_structure.py -q` — Expected: FAIL (`No module named 'aitrader.backtest.structure'`).
- [ ] **Step 3: Implement `aitrader/backtest/structure.py`.** Swing detection may use centered windows, but every derived column reads only swings whose bar ≤ t − 2 (shift by 2). Levels: per-day loop over known swing prices from bars t−59…t−2. EMA20 = `ewm(span=20, adjust=False)`. Choppy: percentile of today's `atr(df,10)/close` among the previous 250 values.
- [ ] **Step 4: Run** the file, then `.venv/Scripts/python -m pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Backtest: causal price-structure features (swings, trend, levels, candles, retest)"`

---

### Task 2: Strategy D signals

**Files:**
- Create: `aitrader/backtest/strategies/price_action.py`
- Modify: `aitrader/backtest/strategies/__init__.py` (register `"price_action": PriceAction`; add to `INDEX_STRATEGIES`)
- Test: `tests/test_bt_price_action.py`

**Interfaces:**
- Consumes: `add_structure` columns (Task 1); `Candidate`, `LONG`, `SHORT`.
- Produces: `class PriceAction` with `key = "price_action"`, `name = "Price action"`, `PARAMS` (all D numbers incl.
  `partial_r=1.5`, `time_stop_days=30`, `min_stop=0.01`, `max_stop=0.08`),
  `DEFAULT_MONEY = {"risk_per_trade": 300.0, "daily_loss_limit": 600.0}`, `exit_policy = "partial_trail"`,
  `__init__(self, use_volume: bool = True)`, `prepare(self, df) -> df` (= `add_structure(df, self.use_volume)`),
  `candidates(day, rows) -> List[Candidate]` (long only), `signals(day, rows, direction) -> List[Candidate]`.
  `facts` include `setup` (`"pullback"`|`"retest"`), `room_r`, `level`, `touches`.
  `score = min(room_r, 5) * 10 + (1 if retest else 0)` so the engine's existing `(-score, symbol)` sort gives
  room first, then retest before pullback, then symbol. Callers treat strategies without `prepare` / `exit_policy`
  as identity / `"fixed_target"`.

- [ ] **Step 1: Write the failing tests** (rows as dicts, like `tests/test_bt_strategies.py`)
  - `test_pullback_to_support_with_hammer`: trend 1, low 100.4, support 100 (3 touches), hammer, close 103 > ema20 101, resistance 115 → one LONG candidate, stop = low, `facts["setup"] == "pullback"`, reason contains "Uptrend", "support", "hammer".
  - `test_pullback_near_misses` (parametrized): trend 0; low 2% from support and ema20; no rejection candle; close ≤ ema20; stop < 1% or > 8%; choppy True → no candidate.
  - `test_room_to_resistance_below_2r_is_skipped`: resistance at close + 1.5R → none.
  - `test_no_level_in_the_way_scores_five`: resistance NaN → `facts["room_r"]` is None and `score == 50`.
  - `test_retest_candidate_and_stop`: `retest_long`, retest_level 101, low 100.6, atr14 2 → stop = min(100.6, 101 − 1.0) = 100.0; trend 0 still allowed.
  - `test_ranking_prefers_room_then_retest`: equal capped room → retest scores higher than pullback.
  - `test_short_mirror_for_index`: downtrend, high within 1% of resistance, shooting star, close < ema20 → SHORT candidate, stop = day high.
  - `test_registry_and_defaults`: `"price_action"` in `STRATEGIES` and `INDEX_STRATEGIES`; `PriceAction.DEFAULT_MONEY` as above.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_bt_price_action.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** per spec §3.4–3.7 and §9.3 (all filters on the signal row; reason text per §6).
- [ ] **Step 4: Run** the file and the full suite — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Backtest: strategy D (price action) signals"`

---

### Task 3: Partial-and-trail exit rule

**Files:**
- Modify: `aitrader/backtest/rules.py`
- Test: `tests/test_bt_rules.py` (append)

**Interfaces:**
- Produces:
  - `partial_trail_check(direction, bar: Bar4, *, stop: float, partial_level: float, partial_done: bool, entry_day: bool, days_held: int, time_stop: int) -> Optional[Tuple[str, float, str]]` → `("partial", price, reason)` or `("exit", price, reason)`; reasons `stop_gap, partial_gap, stop_same_day, stop, trail_stop, partial, time` (`trail_stop` = a stop hit after the partial).
  - `next_trail_stop(direction, stop: float, entry: float, partial_done: bool, low5: float, high5: float) -> float` — unchanged before the partial; after it `max(stop, entry, low5)` for long, `min(stop, entry, high5)` for short.

- [ ] **Step 1: Failing tests** (long; stop 95, partial level 107.5, entry 100):
  - later day open ≤ 95 → `("exit", open, "stop_gap")`; open ≥ 107.5 → `("partial", open, "partial_gap")`; neither applies on the entry day.
  - low ≤ 95 and high ≥ 107.5 same bar → `("exit", 95, "stop_same_day")`.
  - high ≥ 107.5 only → `("partial", 107.5, "partial")`; with `partial_done=True` the same bar → `None`.
  - after the partial, low ≤ stop → `("exit", stop, "trail_stop")`; `days_held == 30` → `("exit", close, "time")`.
  - short mirror: stop 105, partial level 92.5.
  - `next_trail_stop`: before partial unchanged; after partial `(95, entry 100, low5 98) → 100`, then `(100, 100, 103) → 103`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_bt_rules.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** both in `rules.py` (spec §4, §9.2).
- [ ] **Step 4: Run** — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Backtest: partial-profit and trailing-stop exit rule"`

---

### Task 4: Engine and index walker support D

**Files:**
- Modify: `aitrader/backtest/engine.py`, `aitrader/backtest/index_signals.py`
- Test: `tests/test_bt_engine.py`, `tests/test_bt_index_signals.py` (append)

**Interfaces:**
- Consumes: Task 3 functions; strategy `exit_policy`, `PARAMS["partial_r"|"time_stop_days"|"min_stop"|"max_stop"]`; frame columns `low5, high5`.
- Produces:
  - `simulate(..., data_flags=None, blocked_entry_days: Optional[Collection[date]] = None)` — entries on a blocked day are skipped as `event_day`.
  - For `exit_policy == "partial_trail"`: gap skip uses the 1.5R level from the signal close; after the fill, stop distance outside `[min_stop, max_stop]` → skip `stop_distance`; one `Trade` per position with share-weighted `exit_price`, charges = buy + every sell, `facts["partial"] = {"date", "price", "shares"}`; time stop from `PARAMS`; the trailing stop is updated at each day's close via `next_trail_stop`.
  - `index_signals(frame, symbol, days, strategy, money, blocked_entry_days=None)`; for D, `r_multiple = 0.5 × 1.5 + 0.5 × R_rest` when a partial happened, else the single exit's R.

- [ ] **Step 1: Failing tests** (Scripted strategy with `exit_policy = "partial_trail"` and D's `PARAMS`; candidate close 100, stop 95):
  - `test_partial_then_breakeven_exit`: bar reaches 1.5R → half sold; next bar low below entry → rest out at breakeven; one Trade, `exit_reason == "trail_stop"`, weighted exit price, charges = buy + two sells, `facts["partial"]["shares"] == shares // 2`.
  - `test_partial_then_trail_then_time`: trail rises with `low5`; day 30 → `time`.
  - `test_one_share_partial_closes_everything`: size 1 → single sell at 1.5R, `exit_reason == "partial"`, no remainder.
  - `test_stop_distance_rechecked_on_fill`: open so that fill-to-stop > 8% → skip `stop_distance`.
  - `test_event_day_blocks_entry`: `blocked_entry_days={days[1].date()}` → skip `event_day`.
  - `test_fixed_target_strategies_unchanged`: existing target scenario through `simulate` → identical Trade.
  - `test_index_partial_trail_r_is_average_of_halves`: partial, then rest exits at entry → `r_multiple == 0.75`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_bt_engine.py tests/test_bt_index_signals.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement**, branching on `getattr(strategy, "exit_policy", "fixed_target")`; the fixed-target path stays behaviourally identical.
- [ ] **Step 4: Run** the two files, then the full suite — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Backtest: engine and index walker support D's partial/trailing exits and event-day blocks"`

---

### Task 5: RBI dates, runner wiring, warnings

**Files:**
- Create: `aitrader/data/rbi_policy_dates.yaml`, `aitrader/backtest/events.py`
- Modify: `aitrader/backtest/runner.py`
- Test: `tests/test_bt_events.py`, `tests/test_bt_runner.py` (append)

**Interfaces:**
- Produces:
  - YAML: `covered_from: 2016-01-01`, `covered_to: <last verified date>`, `dates: [{date: YYYY-MM-DD, note: "...", source: "<rbi.org.in URL>"}, ...]`.
  - `load_rbi_dates(path: Optional[Path] = None) -> tuple[list[date], Optional[date], Optional[date]]` — missing/empty file → `([], None, None)`.
  - `blocked_entry_days(policy_days, trading_days: Sequence[date]) -> set[date]` — each policy day plus the trading day before it.
  - Runner: calls `strategy.prepare` on stock frames when present; for D passes `blocked_entry_days` and uses `PriceAction(use_volume=False)` (prepared index frames) for index signals; adds warning `"RBI policy dates missing for <from>–<to>; event filter partly off"` when the run range isn't covered.
- [ ] **Step 1: Failing tests**
  - `test_blocked_days_include_day_before`: policy 2024-02-08, trading days …02-07, 02-08 → `{02-07, 02-08}`.
  - `test_missing_rbi_file_warns`: loader on a nonexistent path → `([], None, None)`; runner end-to-end with `price_action` (loader path patched to nonexistent) → status ok and a warning containing "RBI policy dates missing".
  - `test_price_action_runs_end_to_end`: synthetic market from `tests/test_bt_runner.py` with `BacktestRequest("price_action", money=MoneyRules(risk_per_trade=300, daily_loss_limit=600))` → status ok; `summary["index_signals"]` present.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_bt_events.py tests/test_bt_runner.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement**, then **fill the YAML** from RBI's official monetary-policy statements/press releases on rbi.org.in: every announcement 2016 → latest, scheduled and off-cycle, each with its source URL. Only verified dates; `covered_to` = last verified date. If the site can't be reached, leave `dates: []` and record it.
- [ ] **Step 4: Run** the files and the full suite — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Backtest: RBI policy-day filter and strategy D wiring in the runner"`

---

### Task 6: Two-losses-in-a-row notice

**Files:**
- Modify: `aitrader/backtest/metrics.py`
- Test: `tests/test_bt_metrics.py` (append)

**Interfaces:**
- Produces: `loss_streak_alerts(trades: Sequence[Trade]) -> dict` → `{"alerts", "after_trades", "after_win_rate", "after_avg_r"}` per spec §5 / §9.4; `summarize(...)["loss_streaks"] = {"whole": ..., "unseen": ...}` (unseen = entry ≥ unseen_from).
- [ ] **Step 1: Failing tests**
  - `test_loss_streak_alerts`: trades by exit order with P&L `[-1,-1,-1,+2,-1,-1,+1]` and entry dates arranged so the trades after each second loss are known → `alerts == 2` and hand-computed `after_*` values.
  - `test_no_trades_no_alerts`: `[]` → `{"alerts": 0, "after_trades": 0, "after_win_rate": None, "after_avg_r": None}`.
  - `test_summary_has_loss_streaks`: `summarize` output has `loss_streaks.whole` and `.unseen`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_bt_metrics.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Backtest: two-losses-in-a-row notice in every run summary"`

---

### Task 7: Report for D and the notice

**Files:**
- Modify: `aitrader/backtest/report.py`
- Test: `tests/test_bt_report.py` (append)

**Interfaces:**
- Consumes: `summary["loss_streaks"]` (Task 6), trade `facts["partial"]` (Task 4).
- Produces: `NAMES["price_action"] = "D — Price action"`; `RULES["price_action"]` in the plain style of A–C (structure trend, the two setups, filters incl. RBI days, half at 1.5R + breakeven + trail, 30-day limit, ₹300/₹600 defaults); a D note in section 2's exit description; a "⚠ Two losses in a row" subsection: "2 losses in a row happened N times; trades taken right after averaged X R (win rate Y). Trading was not paused — this is a notice only."
- [ ] **Step 1: Failing tests**
  - `test_report_for_price_action_explains_rules`: report of a `price_action` run contains "D — Price action", "higher highs", "1.5R", "breakeven", "RBI".
  - `test_report_shows_loss_streak_notice`: inject `loss_streaks.whole = {"alerts": 3, "after_trades": 5, "after_win_rate": 0.2, "after_avg_r": -0.4}` → report contains "2 losses in a row happened 3 times" and "-0.40R".
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_bt_report.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the file, the full suite and `.venv/Scripts/ruff check .` — Expected: pass, clean.
- [ ] **Step 5: Commit** `git commit -m "Report: strategy D rules and the two-losses notice"`

---

### Task 8: Dashboard

**Files (in `D:\ai-trader`):**
- Modify: `vercel-demo/src/components/BacktestTab.jsx`, `vercel-demo/src/components/backtest/TradeChart.jsx`, `README.md`

**Interfaces:**
- Consumes: strategy key `price_action`, `summary.loss_streaks`, trade `facts.partial`.
- Produces: dropdown "D — Price action"; choosing D sets risk ₹300 / daily stop ₹600, choosing A–C restores ₹400 / ₹1,000; a warning-styled notice in the run view when `loss_streaks.whole.alerts > 0` (same sentence as the report); a "½ sold" marker on the trade chart at `facts.partial.date`; README line about D.
- [ ] **Step 1:** Implement the UI changes.
- [ ] **Step 2: Build** `cd vercel-demo && npm run build` — Expected: success.
- [ ] **Step 3: Browser check** with the restarted service: select D (fields show 300/600), run it on real data, open a trade with a partial (marker visible), see the notice.
- [ ] **Step 4: Commit** `git commit -m "Backtest tab: strategy D, its default risk, two-losses notice, partial-exit marker"`
