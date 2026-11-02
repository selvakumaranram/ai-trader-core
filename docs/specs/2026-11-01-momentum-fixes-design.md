# Momentum fixes and strategy A2 — design

Source: the user's brief of 2026-11-01 (pasted in chat) plus their answers. Run #21 (A — Momentum, 524 trades,
−35.7%) showed a gross edge of about +0.05R per trade that charges (about 0.08R) turn negative, with the worst
losses when NIFTY 50 is below its 200-day average. This work adds risk controls that any strategy can use, a new
medium-term momentum strategy (A2), richer reports and a combined comparison of five runs.

## Must not change
- Execution rules: a signal after the close, entry at the next day's open, a stop and target touched on the same
  day counts as a loss, the gap handling, Zerodha delivery charges and slippage.
- Strategy A stays as it is. Every new behaviour is a money setting. The `MoneyRules` dataclass defaults keep the
  old behaviour, so stored params from old runs reproduce exactly. The new defaults apply only to new runs
  created through the API or the dashboard (`NEW_RUN_MONEY`).
- No parameter tuning: use the values given here.

## New money settings (in every report's money table)
| Setting | Old / dataclass default | New-run default |
|---|---|---|
| Starting capital | ₹20,000 | ₹1,00,000 |
| `risk_mode` | `fixed` (₹ `risk_per_trade`) | `percent` |
| `risk_pct` | — | 1% of equity |
| Daily loss stop | ₹1,000 fixed | `daily_loss_pct` 3% of equity |
| Max open positions | 3 | 5 |
| Max money in one stock | ₹7,000 | ₹25,000 |
| `regime_filter` | off | on |
| `max_charge_to_risk` | off | 0.05 |
| `max_drawdown_pause` | off | 15%, `pause_min_days` 10 |

"Equity" means cash plus open positions valued at the latest close. Sizing for an entry at day t+1 uses the
equity at day t's close. The daily loss stop uses the equity at the previous close.

1. **Regime filter.** NIFTY 50 is "on" at day t when its close is above its 200-day simple average of closes;
   with fewer than 200 days of history it is off. When the filter is enabled and the signal day is off, no new
   **long stock** entries are taken. Each blocked candidate counts as skip reason `regime_off`. Open positions
   keep their normal exits. The filter never blocks short or bearish (PE) ideas: NIFTY/BANKNIFTY index signals
   are left alone in every strategy.
2. **% risk.** risk ₹ = `risk_pct` × equity. shares = floor(risk ÷ (fill − stop)), then the existing
   per-position cap, cash check and half-size rule apply. Fixed mode stays available.
3. **Charge-aware skip.** For the final share count, estimate the buy charges at the fill price, plus the sell
   charges at the entry open less slippage, plus the slippage cost on both sides. If the estimate ÷ ((fill −
   stop) × shares) exceeds `max_charge_to_risk`, skip with reason `charges_too_high`. Real results still use the
   actual exit price.
4. **Drawdown pause.** Track the peak equity. At a close where equity ≤ peak × (1 − 15%), new entries stop from
   the next day (skip reason `drawdown_pause`). Trading resumes at the first close that is at least 10 trading
   days after the pause began **and** where NIFTY 50 is above its 200-day average. On resume the peak resets to
   the equity at that moment (the user's choice (a)). Each pause is logged with its start date, resume date (or
   none), equity at pause and equity at resume. The report shows the number of pauses, the total trading days
   paused and the trades skipped.

## Strategy A2 — Medium-term momentum (`momentum_a2`)
- **Eligible:** at least 260 bars, close > SMA200, close > SMA50, and a 50-day average of close × volume of at
  least ₹10 crore. This comes on top of the engine's universal filter.
- **Score:** percentile ranks (0–100) across the eligible stocks, weighted:
  - 50 × r6, where r6 = close[t−21] ÷ close[t−126] − 1;
  - 30 × r12, where r12 = close[t−21] ÷ close[t−252] − 1;
  - 20 × r6 ÷ (standard deviation of daily returns over the last 126 days).
  - No 1-, 3- or 5-day returns and no RSI.
- **Signals:** only on the last trading day of each ISO week, taken from the run's trading-day list (so a Friday
  holiday makes Thursday the signal day). Candidates are the top 10 by score; free slots are filled in score
  order.
- **Initial stop:** signal close − 2.5 × ATR14. There is no target; the trade table stores NULL as its target.
- **Trailing stop:** after every close, stop = max(previous stop, highest close since entry − 3 × ATR14). It is
  used from the next day under the existing stop and gap rules, with exit reason `trail_stop` once it has risen
  above the initial stop.
- **Time stop:** 60 trading days.
- **Rank exit:** on a weekly signal day, a held stock that is not in the top 30 by score (ineligible counts as not
  in the top 30) or closes below SMA200 is sold at the next open with reason `rank_exit`. If that open is at or
  below the stop, the reason is `stop_gap` instead.
- **Index signals:** none for A2.

## Report additions (every run)
- Gross R, charges in R and net R per trade, each for the earlier, unseen and whole periods.
- A split by regime on the signal day (above or below SMA200): trades, win rate, average R and ₹ P&L, for the
  earlier and unseen periods.
- The average position value, and the % of trades cut below the intended risk by the cap or by cash.
- An exit-reason table and a skip-reason table (with the new reasons), plus the drawdown-pause log.
- The existing disclaimer and the survivorship caveat stay.

## Study script — `python -m aitrader study`
1. Run `scan_history` first.
2. Run five backtests over 2017-08-14 → 2026-10-30, with the last 2 years as the unseen period:
   - **A-base:** momentum with the old settings explicitly. It must reproduce #21: 524 trades, −35.7%.
   - **B-base:** pullback with the old settings. It must reproduce #22: 873 trades, −43.5%.
   - **A-fix:** momentum with the new settings.
   - **A2-100k:** A2 with the new settings.
   - **A2-20k:** A2 with the new settings except a capital of ₹20,000 and ₹7,000 per stock.

   The old settings are: ₹20,000; fixed ₹400 risk; 3 positions; ₹7,000 cap; ₹1,000 daily loss stop; regime
   filter off; no charge skip; no pause.
3. Write each report (.md) with its trades and equity CSVs, plus `comparison.md`, to the output folder.
4. Print the comparison table and the reproduction check to the console.

The comparison has one row per run. Its columns, shown separately for the earlier and unseen periods, are:
return, CAGR, max drawdown, worst month, trades, win rate, net expectancy (R), profit factor, total charges,
charges as a % of gross profit, and the checklist result.

## Dashboard
- A2 is added to the strategy list.
- A "Money mode" choice offers "% of equity (new)" and "Fixed ₹ (old)", with the new fields.
- The new-run defaults above apply.
