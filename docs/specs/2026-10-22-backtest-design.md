# Part 2 — Swing Backtest Engine and Strategies (Design)

Status: draft for review · Date: 2026-10-22 · Builds on: `2026-10-20-data-foundation-design.md` (Part 1)

## 1. Purpose

Replay three swing-trading strategies on stored daily candles, under the user's real money limits, and show
honestly how each would have behaved — so the user can (a) decide which strategies deserve paper trading in
Part 4 and (b) learn each strategy trade by trade (teaching in Part 3 builds on the per-trade reasons recorded here).

Success is an honest, reproducible comparison against simply holding NIFTY 50, judged on years the strategy was
not tuned on. **Nothing here claims or implies future profit.** The software never places orders.

### Out of scope (later parts)

- Option P&L (needs vendor option history) — index results are scored in R only.
- Real-time signals, notifications, paper trading (Part 4); Claude teaching (Part 3).
- Parameter optimisation of any kind.

## 2. Decisions taken with the user

| Topic | Decision |
|---|---|
| Strategies | A = Momentum (user's `Momentum_Based_Short_Term_Stock_Strategy.md`, price/volume parts only), B = Trend pullback, C = Breakout |
| Capital | ₹20,000 starting paper capital (the most the user will commit in real time) |
| Risk limits | ₹400 risk per trade (2%), ₹1,000 daily loss stop, max 3 open positions, ₹7,000 max per position, 1:2 risk/reward |
| Costs | Zerodha equity-delivery charges + slippage, always included |
| Engine | Own day-by-day simulator in Python (not backtesting.py / vectorbt) |
| UI | New "Backtest" tab in the existing React dashboard, visible only when the local service runs |

All money limits are run settings with these defaults; each run stores the settings it used.

## 3. Architecture

New package `aitrader/backtest/` in `D:\ai-trader-core`; new tab in `D:\ai-trader\vercel-demo`.

| Unit | Responsibility | Depends on |
|---|---|---|
| `backtest/bars.py` | Load daily bars (one `price_basis`, source `yfinance`) for active instruments into one pandas DataFrame per instrument, indexed by trade date | `db/repo.py` |
| `backtest/indicators.py` | Pure functions: SMA, ATR(14) (Wilder), RSI(14) (Wilder), rolling N-day high/low (excluding today where stated), average volume, average traded value | pandas |
| `backtest/strategies/{base,momentum,pullback,breakout}.py` | `Strategy` protocol: given all bars up to and including day *t*, return `Candidate`s (symbol, direction, planned stop, score, reason text, rule facts). No money logic. | indicators |
| `backtest/costs.py` | Zerodha delivery charge model + slippage; all rates in a `CostModel` dataclass with defaults | — |
| `backtest/engine.py` | Day-by-day portfolio simulation: fills, sizing, exits, money rules, skip log, equity curve | strategies, costs |
| `backtest/index_signals.py` | Run strategies B and C on NIFTY 50 / NIFTY BANK in both directions, R-scored, no money | strategies, engine exit rules |
| `backtest/metrics.py` | Summary stats, per-period (earlier / unseen) and per-year breakdown, checklist | — |
| `backtest/runner.py` | Orchestrates one run: validate → load → simulate → metrics → persist; one run at a time | all above, repo |
| `db/schema.py` + Alembic migration | Tables `backtest_runs`, `backtest_trades`, `backtest_equity`, `backtest_index_signals` | — |
| `db/repo.py` | All new SQL (insert run, finish run, insert trades/equity, list/get runs, bars for a symbol) | — |
| `api.py` | New endpoints (§8) | runner, repo |
| `vercel-demo/src/components/BacktestTab.jsx` (+ small chart components) | Run panel, run list, run view, trade chart | `lightweight-charts` |

Data flow: Run click → `POST /local/backtests` → run row `running` → background task: load bars → for each
trading day, strategies → engine → persist trades/equity/index signals → metrics → run row `ok` (or `failed` with
message) → tab polls and renders.

## 4. Trading rules (shared)

**Information timing.** Signals use bars up to and including day *t*'s close. Entry is at day *t+1*'s open.
Nothing on or after *t+1* may influence a decision made at *t* (enforced by test, §12).

**Eligibility (stocks).** On day *t* a stock is eligible when it has ≥ 200 bars up to *t* and its 20-day average
traded value (close × volume) ≥ ₹10 crore. Index instruments are never traded in the stock portfolio.

**Direction.** Stocks: long only. Indices (§6): long and short, R-scored.

**Sizing at fill (day *t+1* open, price `O`).**
1. Skip (reason `gap_through_stop`) if `O ≤ stop`; skip (`gap_past_target`) if `O ≥ planned target`, where
   planned target = signal close + 2 × (signal close − stop).
2. Fill price `E = O × (1 + slippage)`.
3. `risk_per_share = E − stop`; shares = ⌊risk_per_trade ÷ risk_per_share⌋, then capped by ⌊max_position ÷ E⌋ and by
   ⌊available cash (after entry charges) ÷ E⌋. Skip (`size_zero`) if shares < 1.
4. Target = `E + 2 × (E − stop)` (risk/reward is always 1:2 on the actual fill).

**Selection.** Each day, candidates are sorted by score (desc, ties by symbol). Skip a candidate if the stock is
already held (`already_held`), slots are full (`no_slot`), cash is insufficient (`no_cash`), or the daily loss pause
is active (`daily_loss_pause`). Skips are counted by reason.

**Exits.** Checked on every bar from the entry day onward (on the entry day, from the open onward).
For a long position on day *d* with open `O`, high `H`, low `L`, close `C`:
1. On days after entry: if `O ≤ stop` → exit at `O` (`stop_gap`); else if `O ≥ target` → exit at `O` (`target_gap`).
2. Else if `L ≤ stop` and `H ≥ target` → exit at stop (`stop_same_day`; conservative, daily bars cannot order them).
3. Else if `L ≤ stop` → exit at stop (`stop`). Else if `H ≥ target` → exit at target (`target`).
4. Else if the position has been held 20 trading days → exit at `C` (`time`).
5. At the end of the backtest range, open positions exit at the last close (`end_of_test`).
Sell fills apply slippage against the seller: `exit × (1 − slippage)`.

**Daily loss pause.** If realised P&L (after charges) of positions closed on day *d* ≤ −₹1,000, no new entries
on day *d+1*.

**Missing bars.** If a held stock has no bar on a trading day, its exit checks wait for the next available bar;
the gap is counted in run notes. A stock with no bar on signal day *t* produces no candidate.

## 5. Strategies

Defaults below are fixed per run and stored in the run's `params`. They are not optimised.

**A — Momentum (price/volume parts of the user's doc).**
- Components, each converted to a percentile rank (0–100) across eligible stocks that day: 21-day return (weight
  30), 5-day return (25), 3-day return (15), 1-day return (10), volume surge = 5-day avg volume ÷ 50-day avg volume
  (10), RSI(14) in [55, 70] → 100 else 0 (5). The doc's delivery-% (5) is unavailable; the weighted sum is divided
  by 95.
- Candidate: among the top 10 scores, with close > SMA(50). Stop = close − 2 × ATR(14). Score = momentum score.
- Reason text example: "Ranked #3 of 184 on momentum (21-day +12.4%, 5-day +4.1%, volume 1.8× normal, RSI 63);
  above its 50-day average. Stop 2×ATR below."

**B — Trend pullback.**
- Candidate when: close > SMA(50) > SMA(200); low ≤ SMA(20) × 1.01; close > SMA(20); close > open.
- Stop = lowest low of the last 3 bars (including *t*). Skip if (close − stop)/close < 1% or > 8%.
- Score = (SMA(50) − SMA(200)) ÷ SMA(200).

**C — Breakout.**
- Candidate when: close > highest high of the 20 bars before *t*; volume ≥ 1.5 × 20-day avg volume (excluding *t*);
  close > SMA(50).
- Stop = that 20-bar high − 1 × ATR(14). Skip if stop ≥ close or (close − stop)/close > 8%.
- Score = volume ÷ 20-day avg volume.

Every candidate carries `facts` (the numbers its rule used) and a one-to-two sentence `reason` built from them.

## 6. Index signals (NIFTY 50, NIFTY BANK)

Strategies B and C only, on index bars, without volume conditions, in both directions:
- **Bullish (CE idea)** — rules as §5.
- **Bearish (PE idea)** — mirrors: B: close < SMA(50) < SMA(200); high ≥ SMA(20) × 0.99; close < SMA(20);
  close < open; stop = highest high of last 3 bars. C: close < lowest low of prior 20 bars; stop = that low + 1 × ATR(14).
- Entry at next open, exits per §4 mirrored for shorts, 20-day time stop, at most one open signal per index per
  direction. No slippage, no costs.
- Result per signal in R = (exit − entry) ÷ (entry − stop) for long, mirrored for short.
- Labelled everywhere: **"Index signal quality in R — not option profit."**

## 7. Costs (Zerodha equity delivery; defaults, configurable)

| Charge | Default |
|---|---|
| Brokerage | ₹0 |
| STT | 0.1% of value, buy and sell |
| Exchange transaction (NSE) | 0.00297% of value |
| SEBI fee | ₹10 per crore |
| Stamp duty | 0.015% of buy value |
| GST | 18% of (brokerage + exchange + SEBI) |
| DP charge | ₹15.93 per stock per sell day (incl. GST) |
| Slippage | 0.05% per side (applied to fill price) |

Defaults are checked against Zerodha's published charges page during implementation; the rates actually used are
stored in each run's `params`.

## 8. Storage and API

**Tables** (new Alembic migration):
- `backtest_runs`: id, strategy, start_date, end_date, unseen_from, params (JSONB: money, costs, strategy defaults),
  status (`running`/`ok`/`failed`), message, summary (JSONB), started_at, finished_at.
- `backtest_trades`: id, run_id, instrument_id, symbol, signal_date, entry_date, entry_price, shares, stop, target,
  exit_date, exit_price, exit_reason, charges, pnl, r_multiple, reason, facts (JSONB), period (`earlier`/`unseen`).
- `backtest_equity`: run_id, trade_date, cash, invested_value, equity, benchmark_equity. PK (run_id, trade_date).
- `backtest_index_signals`: id, run_id, symbol, direction, signal_date, entry_date, entry, stop, target, exit_date,
  exit, exit_reason, r_multiple, reason, period.
- Skips are kept as counts by reason in `summary` (not per row).

**Endpoints** (all on the loopback service):
- `POST /local/backtests` body `{strategy, start?, end?, capital?, risk_per_trade?, daily_loss_limit?, max_positions?,
  max_position_value?}` → 202 `{id}`. 409 if a backtest is running. 400 on bad input.
- `GET /local/backtests?limit=` → runs, newest first, with summary headline numbers.
- `GET /local/backtests/{id}` → run, summary, equity curve, trades, index signals. 404 if unknown.
- `GET /local/bars/{symbol}?start=&end=` → daily candles for the trade chart. 404 if unknown symbol.

**Startup:** any `backtest_runs` row still `running` is set to `failed` with message "interrupted (service restarted)".

## 9. Metrics and judgement

- Unseen period = the last 2 years of the run's range (`unseen_from` = end − 2 years); the rest is "earlier".
  Trades belong to the period of their entry date.
- Per run and per period: final equity, total return %, CAGR, max drawdown (% and ₹), longest losing streak,
  worst month, trade count, win rate, avg win / avg loss (₹ and R), expectancy (mean R after charges), profit factor,
  total charges and charges ÷ gross profit, % of days invested, skips by reason.
- Benchmark: same capital in NIFTY 50 (`^NSEI`) bought at the first day's close, marked to close daily. Labelled
  "price index — excludes dividends".
- Per calendar year: return, max drawdown, trade count.
- **Minimum-evidence checklist** (shown as ticks; explicitly "not a promise of profit"):
  1. ≥ 30 trades in the unseen period;
  2. expectancy > 0 after charges in both periods;
  3. max drawdown ≤ 20% over the whole run;
  4. unseen period beats the benchmark on return or on max drawdown.
- Warnings on every run: survivorship bias (today's Nifty 200 list applied to the past); index R ≠ option profit.

## 10. Backtest tab (dashboard)

Shown only when `/local/status` answers (same rule as the Data tab).
1. **Run panel** — strategy, date range (default: all available), money settings pre-filled with defaults, Run button,
   status line while running.
2. **Runs list** — date, strategy, range, return, max drawdown, checklist ticks; click to open.
3. **Run view** — summary (earlier vs unseen side by side), checklist, warnings, equity vs NIFTY line chart with the
   unseen boundary marked, per-year table, skip counts, index signals table.
4. **Trades table** — symbol, dates, entry, stop, target, exit, exit reason, ₹, R, reason. Clicking a trade opens a
   candlestick chart (60 bars before the signal to 10 bars after the exit) with entry, stop, target and exit marked.

Charts use `lightweight-charts` pinned to an exact version. Everything else is plain React matching existing tabs.

## 11. Errors and edge cases

- No instruments or bars → run `failed`: "Load the stock list and price history first (Data tab)".
- `start` defaults to the first date where NIFTY 50 has ≥ 200 bars; an explicit `start` earlier than that → 400.
- Concurrent run → 409. Restart mid-run → `failed`/"interrupted".
- Bad input (unknown strategy, end ≤ start, non-positive money values, risk_per_trade > capital) → 400.
- Missing bars → §4 rule, counted in notes.
- Any exception inside a run → `failed` with the exception type only (no stack traces or secrets in the message).

## 12. Testing

All engine tests use small synthetic candle sets with hand-derived expected results.
- Indicators vs hand-calculated values (SMA, Wilder ATR/RSI, rolling highs excluding today).
- Each strategy: a constructed series that triggers it and near-misses that must not.
- Engine: gap through stop (skip), gap past target (skip), stop and target same day (loss), each sizing cap, cash
  shortage, full slots, already held, daily-loss pause, time stop, end-of-test exit, missing bar.
- **No-lookahead test:** mutate all bars after day *t*; every decision up to *t* must be identical.
- Costs: one worked buy/sell example matching the §7 rates.
- Metrics: known equity curves (drawdown, CAGR, streaks), period split, checklist.
- Index signals: one bullish and one bearish synthetic case, R values exact.
- Runner end-to-end on a synthetic 5-stock market with the exact expected trade list.
- API against the test database (202/409/400/404, interrupted-run recovery). Dashboard: `npm run build` passes.

## 13. Change to Part 1 needed

`backfill_history` defaults to 5 years. With a 200-bar warm-up and 2 unseen years, that leaves about 2 "earlier"
years — too little. **Raise the backfill default to 10 years** (and the universe's assumed membership start
accordingly). Longer history increases survivorship bias; the warning covers it.

## 14. Deferred

- Delivery %, ASM/GSM and market-cap filters from the user's doc (need new data sources).
- Index option P&L (vendor history), real-time signals, teaching.
