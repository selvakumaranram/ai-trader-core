# Strategy D — Price Action (Design)

Status: draft for review · Date: 2026-10-27 · Builds on: `2026-10-22-backtest-design.md` (Part 2)

## 1. Intent

The user shared trading notes built around **price action**: read market structure (higher highs / higher lows),
trade pullbacks to support and breakout-retests with a defined stop, take only setups with at least 1:2
reward, book half the profit early and let the rest run. They want these ideas turned into a fourth backtestable
strategy, **D — Price action**, alongside A (momentum), B (pullback) and C (breakout), so they can learn it and
compare it honestly.

**User decisions (theirs):**
- Same daily-candle, multi-day (swing) style as A/B/C. Everything intraday in the notes is **dropped**: 5/15-minute
  charts, avoiding the first 15 minutes and the 11:30–13:30 lull, pre-market GIFT Nifty checks.
- Applies to the **Nifty 200 stocks and to NIFTY 50 / NIFTY BANK** (index signals bullish = CE idea, bearish = PE
  idea, scored in R as today).
- **"Stop after 2 consecutive losses" is NOT enforced** — trading continues; the user is only **notified**, on the
  dashboard for now (email / SMS / WhatsApp later, out of scope).
- Risk: D defaults to the notes' numbers; the user will also run it with the current settings to compare.
- PCR (put-call ratio) sentiment is **held back** — no option-chain history exists yet.

**Goals:** a faithful, explainable, look-ahead-free daily version of the notes' rules; per-trade reasons that teach
the setup; a fair comparison with A/B/C and NIFTY buy-and-hold.
**Non-goals:** intraday trading, live/paper signals (Part 4), external notifications, PCR, parameter optimisation.
**Anti-goals:** any profit promise; any rule that peeks at future bars (swing points in particular — §3.1);
changing how A/B/C behave.

## 2. Constraints carried over

Everything in the Part 2 spec still applies unless this document overrides it: entry at the next day's open,
eligibility (≥ 200 bars, ≥ ₹10 crore 20-day traded value), Zerodha costs and slippage, gap and same-day rules
(stop before target when both are touched), daily loss pause, slots / cash / `MIN_FILL_FRACTION`, the history-scan
data flags, earlier/unseen split, checklist, warnings, reports and downloads. Stocks are long-only; indices are
long and short, R-scored, no costs.

## 3. Strategy rules

All numbers below are fixed defaults stored in the run's `params` (not optimised). Items marked *(designer's call)*
were decided by the designer, not the user.

### 3.1 Swing points (no look-ahead)

- A **swing high** at bar *i*: `high[i]` is the strictly highest high of bars *i−2 … i+2*. A **swing low** likewise
  with lows. *(designer's call: 2 bars each side)*
- A swing point at bar *i* is only **known from bar i+2's close onward**. Any rule evaluated at day *t* may use only
  swing points with *i + 2 ≤ t*. This must be enforced in code and covered by the no-lookahead test.

### 3.2 Trend (market structure)

At day *t*, using the last two known swing highs (H1 earlier, H2 later) and the last two known swing lows (L1, L2):
- **Uptrend:** H2 > H1 **and** L2 > L1 **and** close > L2.
- **Downtrend:** H2 < H1 **and** L2 < L1 **and** close < H2.
- Otherwise **sideways → no trade.**

### 3.3 Support / resistance levels *(designer's call)*

- Candidate prices: known swing highs and lows from the last 60 bars.
- Cluster candidates whose prices are within 1% of each other; a cluster's level = mean price, **touches** = number
  of swing points in it.
- A **qualifying level** has ≥ 2 touches. Support = qualifying level below the close; resistance = above.

### 3.4 Setup 1 — Pullback to support (long; mirror for index shorts)

At day *t* (signal day), all of:
1. Uptrend (§3.2).
2. **Pullback to value:** the day's low is within 1% of a qualifying support level **or** of the 20-day EMA.
3. **Rejection candle** on day *t*, either:
   - **Hammer:** lower wick ≥ 2 × body, close in the upper third of the day's range, range > 0; or
   - **Bullish engulfing:** day *t* green, day *t−1* red, *t*'s body covers *t−1*'s body.
4. Close > 20-day EMA.
- **Stop:** the low of day *t*. Skip if the stop is < 1% or > 8% below the close.

### 3.5 Setup 2 — Breakout and retest (long; mirror for index shorts)

1. **Range:** the 20 bars before the breakout day span ≤ 12% ((max high − min low) ÷ min low). Range high = max high.
2. **Breakout day b:** close > range high, and (stocks only) volume ≥ 1.5 × the 20-day average volume before *b*.
3. **Retest day t** (b < t ≤ b + 5): the low comes within 1% of the range high (may dip up to 1% below it) **and**
   the close is above the range high. Signal at *t*'s close. Only the first retest per breakout counts.
4. The trend filter does not apply to setup 2 (a range breakout starts a trend), but the choppy filter (§3.6) does.
- **Stop:** the lower of day *t*'s low and (range high − 0.5 × ATR(14)). Skip if < 1% or > 8% below the close.

### 3.6 No-trade filters

- **Choppy / narrow range** *(designer's call)*: skip if ATR(10) ÷ close is in the lowest 20% of its own values over
  the previous 250 bars (needs ≥ 250 bars; otherwise the filter passes).
- **Room to target (the notes' "at least 1:2"):** skip if the nearest qualifying resistance (support for shorts) is
  closer than 2R from the signal close. No qualifying level in the way → passes.
- **RBI policy days:** no new entries whose **entry day** is an RBI monetary-policy announcement day or the trading
  day before it. Dates come from a new editable file `aitrader/data/rbi_policy_dates.yaml` (like the holiday file).
  The builder fills it from RBI's official MPC press releases (rbi.org.in) for 2016 → the latest announced date,
  with a source note per year — **dates must not be guessed**. If the file does not cover a run's whole range, the
  run adds a warning ("RBI dates missing for …; event filter partly off").

### 3.7 Ranking and money

- When several candidates compete for slots: higher **room to target in R** first (capped at 5), ties by setup 2
  before setup 1, then symbol. *(designer's call)*
- **D's default money settings:** risk per trade **₹300 (1.5%)**, daily loss stop **₹600 (3%)**; capital ₹20,000,
  3 positions, ₹7,000 per position unchanged. The Backtest tab pre-fills these when D is selected (A/B/C keep
  ₹400 / ₹1,000). The user can override per run.

## 4. Exits for D (A/B/C unchanged)

- **Initial stop** as per setup. Initial risk R = entry − stop.
- **Partial profit:** when the high touches entry + 1.5R, sell **half** the shares (rounded down) at that price
  (slippage and full sell charges incl. DP apply). Then move the stop to **entry (breakeven)**.
  If the position has only 1 share, sell it all at 1.5R.
- **Trailing the rest:** after the partial, at each close the stop becomes
  max(current stop, lowest low of the last 5 bars), effective from the next day. *(designer's call)*
- No fixed 2R target for D. **Time stop:** exit the remainder at the close after **30** trading days.
- Same-day ambiguity stays conservative: if a day touches both the (current) stop and the 1.5R level, count the stop
  first. Gap rules unchanged (open beyond the stop exits at the open).
- **Recording:** one trade row per position. `exit_price` = share-weighted average exit; `exit_reason` = the final
  exit's reason; `charges` include both sells; `pnl` and `r_multiple` (pnl ÷ initial rupee risk) cover the whole
  position; `facts.partial` = {date, price, shares}. The trade chart marks the partial exit too.
- Index signals for D use the same exit logic in R (half at 1.5R, rest trailing), R = average of the two halves.

## 5. "Two losses in a row" notification (not a rule)

- Trades are ordered by exit date (then entry date, symbol). Every time two consecutive closed trades are both losses
  (pnl ≤ 0), that is one **alert**.
- The backtest never pauses for it. The summary records: number of alerts, and the trades **entered after an alert
  and before the next win** — their count, win rate and average R — so the user can see whether pausing would have
  helped.
- Shown in the run view and the report as a highlighted notice ("⚠ 2 losses in a row happened N times; trades taken
  right after averaged X R"). Computed for every strategy (A–D), since it is cheap and useful for comparison.
- The live dashboard notification arrives with Part 4 (paper signals); external channels later.

## 6. Teaching text

Each D trade's `reason` names the setup and the evidence, e.g.
"Uptrend (higher high 1,312 on 12 Mar, higher low 1,236 on 21 Mar). Pulled back to support 1,240 (3 touches) and
printed a hammer. Stop 1,228 (candle low). Room to next resistance 1,330: 3.4R." `facts` hold the numbers.
The report gets a rules section for D written in the same plain style as A–C.

## 7. Scope of changes (for the builder)

- New strategy module (D) registered with key `price_action`, label "D — Price action", index-signal capable.
- Swing/level/candle helpers as small pure functions with their own tests.
- Engine: per-strategy exit policy (fixed 2R target for A/B/C; partial + trailing for D) without changing A/B/C
  results — existing engine tests must pass unchanged.
- RBI dates file + loader + filter; streak-alert summary; report, tab dropdown, default money pre-fill, run-view
  notice, trade-chart partial marker.

## 8. Testing (minimum)

- Swing points: known series; a swing point is invisible before *i + 2*.
- Trend, levels (clustering, touches), hammer/engulfing (positives and near-misses).
- Each setup: one constructed series that triggers and near-misses for every condition; room-to-target skip.
- Choppy filter, RBI filter (entry day and day before), missing-dates warning.
- Exits: partial at 1.5R then breakeven stop, trailing update, 1-share case, stop-first same-day rule, time stop,
  weighted exit price and charges with two sells.
- No-lookahead test extended to D (mutate bars after *t*; decisions up to *t* identical).
- Streak alerts on a known trade sequence. A/B/C regression: their existing tests unchanged and green.

## 9. Details settled after the builder review *(designer's calls — all chosen to be conservative)*

1. **Entry and gaps (D):** skip if the next open is at or below the stop (`gap_through_stop`) or at or above the
   1.5R partial level measured from the signal close (`gap_past_target`). After the fill, re-check the stop
   distance on the actual fill price: skip (`stop_distance`) if it is outside 1–8%. R is always measured from the
   actual fill. Room-to-resistance stays judged on the signal close.
2. **The partial-fill day:**
   - The partial may fire on the entry day (from the open onward), like A–C's target.
   - On later days, an open at or above 1.5R sells half **at the open** (`partial_gap`), mirroring `target_gap`.
   - If the same bar touches both the original stop and 1.5R, the stop counts first (no partial).
   - After a partial, the breakeven stop applies **from the next day**, like the trailing stop. The same bar is
     not re-checked against breakeven: daily candles can't tell the order of events.
3. **One breakout:** day *b* is a breakout only if its close is above its 20-bar range high **and** the previous
   close was not above that previous day's range high. Each breakout opens one 5-day retest window and gives at most
   one signal. The choppy filter and the room-to-target check are evaluated on the signal (retest) day *t*.
4. **Two-losses alert:**
   - A streak of consecutive losses (by exit order) produces **one** alert when it reaches 2 losses; 3 or more in a
     row is still one alert.
   - "Trades after an alert" = trades whose entry date is after the second loss's exit date, up to and including
     the first later trade (by exit order) that wins.
   - Stock trades only; reported for the whole run and the unseen period.
5. **RBI dates file:**
   - Include every policy announcement, scheduled and off-cycle (e.g. 27 Mar 2020, 22 May 2020, 4 May 2022), and the
     2016 pre-MPC policy reviews.
   - The builder may fetch them from rbi.org.in during the build. Whatever cannot be verified is left out, and the
     "RBI dates missing" warning covers the gap. This is not a blocker.
6. **Index signals (D):**
   - All of D's filters (choppy, room-to-target, RBI) apply to NIFTY / BANKNIFTY too, except the volume condition.
   - Time stop is 30 days, as for stocks.
   - If both setups fire on the same day, the §3.7 ranking picks one.
   - The index walker must give D the bar history it needs. This is a structural change, accepted.

## 10. Out of scope / later

Intraday version, PCR sentiment, live/paper signals and dashboard alert delivery (Part 4), email/SMS/WhatsApp
notifications, tuning any number above.
