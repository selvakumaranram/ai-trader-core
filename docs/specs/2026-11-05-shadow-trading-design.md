# Shadow trading — design

Agreed with the user in chat on 2026-11-05.

## Why
- **The money plan:** the user plans 80–90% of their money for long-term investing and 10–20% for rare, high-conviction
  NIFTY option trades (at most about 50 a year, CE when a strong up-move is expected, PE for a strong down-move).
- **What history says:** the backtests show no proven rule yet. The 2-of-3 index-rule check was a coin flip on
  2017–2026 NIFTY data.
- **What this builds:** a **shadow trading** system that records pretend option trades with **live Kite prices** for a
  year, for several candidate rules side by side, plus the user's own agree/disagree call on each signal. It shows
  where each rule (and the user's judgement) worked and failed.

## Hard rules
- **No orders, ever.** Only Kite `instruments` and `quote` are used. The existing test that forbids order methods
  stays.
- **Paper only.** Every number is labelled "shadow" and approximate.

## Candidate rules (NIFTY 50, decided on the daily close)
All rules share one exit plan on the underlying: stop = close ∓ 1 × ATR(14), target = close ± 2 × ATR(14), at most 5
trading days, never held into the last day before expiry.

| key | CE (up) when | PE (down) when |
|---|---|---|
| `agree2` | ≥ 2 of the B, C, D index rules give a bullish signal and NIFTY > its 200-day average | ≥ 2 bearish and NIFTY < its 200-day average |
| `big_move` | the close is ≥ 1.5% above the previous close and in the top quarter of the day's range | ≥ 1.5% below, in the bottom quarter |
| `gap_hold` | opens ≥ 1% above the previous close and closes at or above the open | opens ≥ 1% below and closes at or below the open |
| `sharp_dip` | NIFTY > its 200-day average and the close is ≥ 2 × ATR(14) below its 20-day average | NIFTY < its 200-day average and the close is ≥ 2 × ATR(14) above its 20-day average |

A **replay** runs every rule over the NIFTY history:
- It reports the days per year, how often NIFTY moved the signal's way within 5 days, the average move, and how often
  the underlying hit the target before the stop.
- Results are shown separately for the earlier years and the last 2 years.

The replay is evidence to read, not a filter: all four rules are shadow-traded live.

## Daily flow (IST, via the existing 1-minute catch-up scheduler)
1. **16:40 `shadow_scan`** (after the 16:30 price load):
   - It evaluates every rule on today's NIFTY bar and stores one signal per rule that fires.
   - It skips if today's bar isn't loaded yet.
   - Yesterday's signals that never got an entry are marked **missed**.
2. **09:20 `shadow_enter`** (needs the Kite login; retried once the user logs in):
   - For each signal from the previous trading day, it "buys" **one lot** of the at-the-money NIFTY option (CE or PE)
     with the nearest expiry at least 7 days away.
   - The entry is at the live `last_price`, and the trade stores the underlying price, stop, target, lot size and
     contract.
   - An entry after 10:30 is marked late.
3. **15:25 `shadow_exit`** (needs the login):
   - For each open trade it quotes NIFTY and the option.
   - It exits at the option's live price when the underlying is at or through the stop or target, after 5 trading
     days, or when the expiry is the next day or sooner.
   - Otherwise it records the latest mark.
   - Underlying checks happen once a day at 15:25, not intraday.

## Profit and loss
P&L per lot = (exit − entry) × lot size − estimated charges. The charge estimate, labelled approximate:
- ₹20 brokerage per order;
- STT 0.1% of the sell premium;
- exchange charge 0.03503% of the premium turnover;
- SEBI ₹10 per crore;
- stamp duty 0.003% of the buy;
- GST 18% on brokerage, exchange and SEBI charges.

## User's view
Each signal has **I agree / I don't** buttons, stored with a timestamp. The scoreboard shows the results of signals
the user agreed with against those they didn't.

## Shadow Trading tab (dashboard)
- A scoreboard per rule: trades, win rate, total and average P&L per lot.
- The user's calls.
- Open trades with their latest mark, and closed trades.
- Signals, including missed ones.
- The replay table.

## Not in scope (later)
- News in the rules.
- BANKNIFTY.
- Intraday stops.
- Real orders (never in this phase).
