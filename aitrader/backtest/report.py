"""Study report for one finished backtest run: Markdown for reading (or pasting into an AI) plus CSVs."""
from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Callable, List

from aitrader.backtest import analysis

NAMES = {"momentum": "A — Momentum", "pullback": "B — Trend pullback", "breakout": "C — Breakout",
         "price_action": "D — Price action", "momentum_a2": "A2 — Medium-term momentum",
         "pullback_b2": "B2 — Trend pullback, volatility-based stop",
         "breakout_c2": "C2 — Breakout, entry-checked, trailing exit",
         "pullback_b3": "B3 — Trend pullback, let winners run",
         "breakout_c3": "C3 — Breakout, trend-confirmed"}

RULES = {
    "pullback": """\
**Idea:** buy a strong stock when it briefly dips inside its uptrend, then turns back up.

A stock becomes a candidate at the day's close when **all** of these are true:
1. **Uptrend:** close > 50-day average > 200-day average.
2. **Pullback:** the day's low came within 1% of the 20-day average, or dipped below it.
3. **Bounce:** the close is back above the 20-day average, and it is a green candle (close > open).
4. **Sensible stop:** the stop is the lowest low of the last 3 days; it must be between 1% and 8% below the close.

**Ranking when there are more candidates than free slots:** stronger trend first,
measured as (50-day avg − 200-day avg) ÷ 200-day avg.""",
    "momentum": """\
**Idea:** buy the stocks that have been rising fastest recently, expecting the strength to continue a little longer
(the price/volume part of the user's "Momentum-Based Short-Term Stock Selection Strategy").

Every evening all eligible stocks are ranked against each other (percentile 0–100) on:
21-day return (weight 30), 5-day return (25), 3-day return (15), 1-day return (10),
volume surge = 5-day avg volume ÷ 50-day avg volume (10), and RSI(14) between 55 and 70 (5).
The doc's delivery-% factor (5) has no data source yet, so the total is divided by 95.

A stock becomes a candidate when it is in the **top 10** by this score **and** closes above its 50-day average.
**Stop:** entry − 2 × ATR(14) (ATR = the stock's average daily price range over 14 days).
**Ranking for free slots:** highest momentum score first.""",
    "breakout": """\
**Idea:** buy when a stock closes at a new 20-day high on heavy volume — a sign that buyers are taking control.

A stock becomes a candidate at the day's close when **all** of these are true:
1. **Breakout:** close > the highest high of the previous 20 days.
2. **Volume:** today's volume ≥ 1.5 × the average volume of the previous 20 days.
3. **Trend filter:** close > 50-day average.
4. **Stop:** the breakout level − 1 × ATR(14); skipped if that is more than 8% below the close.

**Ranking for free slots:** strongest volume surge first.
For NIFTY / BANKNIFTY the same rules run without the volume condition, in both directions
(bullish breakout = CE idea, bearish breakdown below the 20-day low = PE idea).""",
    "price_action": """\
**Idea:** read the market's structure from price alone, and only buy a pullback or a retest when the trend, a
level and the candle all agree — and only when there is room for at least 2× the risk.

**Trend from structure:** a *swing high/low* is a bar higher/lower than the 2 bars on each side (it only counts
once those 2 later bars have closed). **Uptrend** = the last two swing highs are higher highs **and** the last two
swing lows are higher lows, with the close above the latest swing low. Downtrend is the mirror. Otherwise
sideways → no trade.

**Setup 1 — pullback to support:** in an uptrend the day's low comes within 1% of a support level (swing points
from the last 60 days clustered within 1%, at least 2 touches) or of the 20-day EMA, and the day prints a
**hammer** or a **bullish engulfing** candle, closing above the 20-day EMA. Stop = the candle's low.

**Setup 2 — breakout and retest:** a 20-day range spanning ≤ 12% is broken by a close above its high (stocks: on
≥ 1.5× average volume); within 5 days price comes back to within 1% of that high and closes above it.
Stop = the lower of that day's low and (range high − 0.5 × ATR).

**Filters:** stop 1–8% away; skip choppy markets (daily range in the quietest 20% of the past year); skip when the
nearest resistance is less than 2R away; no new entries on RBI policy-announcement days or the session before.

**Exits:** sell **half at 1.5R**, then move the stop to **breakeven** (from the next day) and trail the rest below
the lowest low of the last 5 days; anything left is sold after 30 trading days.
**Default risk:** ₹300 per trade and a ₹600 daily loss stop (1.5% / 3% of ₹20,000).
**Ranking for free slots:** most room to the next level first (capped at 5R), retests before pullbacks.""",
    "momentum_a2": """\
**Idea:** hold the stocks that have been strongest over the last 6–12 months (ignoring the most recent month,
which tends to reverse), and let winners run with a trailing stop instead of a fixed target.

**Eligible:** at least 260 trading days of history, close above both its 50- and 200-day averages, and a 50-day
average traded value (close × volume) of at least ₹10 crore.

**Score** (percentile rank 0–100 across eligible stocks, weighted): 6-month return skipping the latest month,
close[t−21] ÷ close[t−126] − 1 (weight 50); 12-month return skipping the latest month, close[t−21] ÷
close[t−252] − 1 (30); that 6-month return ÷ the standard deviation of daily returns over 126 days (20).
No 1-, 3- or 5-day returns and no RSI.

**When:** signals only on the **last trading day of each week**; candidates are the top 10, and free slots are
filled in score order. **Stop:** entry − 2.5 × ATR(14).
**Ranking for free slots:** highest score first.""",
    "pullback_b2": """\
**Idea:** strategy B's pullback entry, with a stop sized to the stock's normal daily movement and a filter for
stocks stronger than the market.

A stock becomes a candidate at the day's close when **all** of these are true:
1. **Uptrend:** close > 50-day average > 200-day average.
2. **Pullback:** the day's low came within 1% of the 20-day average, or dipped below it.
3. **Bounce:** the close is back above the 20-day average, and it is a green candle (close > open).
4. **Stronger than the market:** the stock's 126-day return is greater than NIFTY 50's 126-day return that day.

**Stop:** the lower of the last 3 days' lowest low and (close − 1.5 × ATR(14)), so it is never closer than
1.5 ATR. **At entry** (next day's open, the actual price): skipped unless entry − stop is between 1 × and 3 ×
ATR(14). There is no fixed 1–8% band.
**Ranking for free slots:** stronger trend first, measured as (50-day avg − 200-day avg) ÷ 200-day avg.""",
}

RULES["breakout_c2"] = """\
**Idea:** strategy C's breakout entry, but the stop distance is checked at the actual entry price (which also blocks
big gap-ups), and winners are allowed to run with a trailing stop instead of a fixed 2R target.

A stock becomes a candidate at the day's close when **all** of these are true:
1. **Breakout:** close > the highest high of the previous 20 days.
2. **Volume:** today's volume ≥ 1.5 × the average volume of the previous 20 days.
3. **Trend filter:** close > 50-day average.

**Stop:** the breakout level − 1 × ATR(14), with no stop-distance cap at the signal (C's 8% check is removed).
**At entry** (next day's open, the actual price): skipped unless entry − stop is between 1 × and 3 × ATR(14).
**Ranking for free slots:** strongest volume surge first.
For NIFTY / BANKNIFTY the table uses C's original index rules and is for research only."""

RULES["pullback_b3"] = RULES["pullback_b2"].replace(
    "**Idea:** strategy B's pullback entry, with a stop sized to the stock's normal daily movement and a filter for\n"
    "stocks stronger than the market.",
    "**Idea:** B2's entry exactly, but let winners run: no fixed target, a trailing stop, a longer time stop, and no\n"
    "quick re-entry into a stock that just stopped out.") + """

**Exits:** no target; trailing stop 3 × ATR(14) below the highest close since entry; time stop 40 trading days.
**Re-entry cooldown:** after a stock exits on a stop or stop gap, no new entry in that stock for 10 trading days."""

RULES["breakout_c3"] = RULES["breakout_c2"].replace(
    "**Stop:** the breakout level − 1 × ATR(14), with no stop-distance cap at the signal (C's 8% check is removed).",
    "**Stop:** the lower of (breakout level − 1 × ATR(14)) and (signal-day close − 1.5 × ATR(14)), so it is never "
    "closer than 1.5 ATR; no stop-distance cap at the signal.") + """

**Short-term market filter:** no new long entries unless NIFTY 50 closes above **both** its 50-day and 200-day
averages on the signal day. **Re-entry cooldown:** 10 trading days after a stop or stop gap in the same stock.
**Exits:** no target; trailing stop 3 × ATR(14) below the highest close since entry; time stop 40 trading days."""

SKIP_NOTES = {
    "regime_off": "NIFTY 50 closed at or below its 200-day average on the signal day (long entries only)",
    "charges_too_high": "estimated round-trip charges + slippage were more than the allowed share of the rupees "
                        "risked",
    "drawdown_pause": "equity was too far below its peak (new entries paused)",
    "stop_distance_at_entry": "entry − stop at the actual entry price was outside 1–3 × ATR",
    "weaker_than_nifty": "the stock's 126-day return did not beat NIFTY 50's",
    "cooldown_after_stop": "the stock stopped out within the re-entry cooldown",
    "short_regime_off": "NIFTY 50 was not above both its 50- and 200-day averages on the signal day",
}

TRAILING = {"momentum_a2", "breakout_c2", "pullback_b3", "breakout_c3"}  # no fixed target: trailing stop + time stop

QUESTIONS = [
    "Compare profit before and after charges. What does the gap say about trade size, stop distance and the "
    "per-position cap for a small account?",
    "Losses often average more than −1R even though the stop is −1R. Which effects cause that (gaps, slippage, "
    "charges, the same-day rule)?",
    "How do the unseen (last 2) years compare with the earlier years? What changed in the market?",
    "Many signals were skipped for lack of a free slot. Is the ranking choosing the best ones?",
    "Pick five trades from the trades CSV (best, worst, typical) and explain why each was taken and why it ended "
    "the way it did.",
    "What single change would you test next, and how would you avoid fitting the past (choose using the earlier "
    "years only, then check the unseen years)?",
]


def pct(v) -> str:
    return "—" if v is None else f"{float(v) * 100:.1f}%"


def rupees(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{'-' if v < 0 else ''}₹{abs(v):,.0f}"


def num(v) -> str:
    return "—" if v is None else f"{float(v):.2f}"


def plain(v) -> str:
    return "—" if v is None else str(v)


ROWS: List[tuple] = [
    ("Return", "return_pct", pct), ("Yearly return (CAGR)", "cagr", pct),
    ("Biggest fall from a peak", "max_drawdown_pct", pct), ("Biggest fall (₹)", "max_drawdown_amount", rupees),
    ("Worst month", "worst_month", pct), ("Trades", "trades", plain), ("Win rate", "win_rate", pct),
    ("Average win", "avg_win", rupees), ("Average loss", "avg_loss", rupees), ("Average win (R)", "avg_win_r", num),
    ("Average loss (R)", "avg_loss_r", num), ("Expectancy (R per trade, after charges)", "expectancy_r", num),
    ("Profit factor", "profit_factor", num), ("Charges paid", "total_charges", rupees),
    ("Longest losing streak", "longest_losing_streak", plain), ("Days with money invested", "pct_days_invested", pct),
]


def file_stem(run: dict) -> str:
    return f"backtest-{run['id']}-{run['strategy']}"


def report_markdown(run: dict) -> str:
    s = run["summary"]
    trades = run["trades"]
    money = (run.get("params") or {}).get("money", {})
    bench = s.get("benchmark") or {}
    L: List[str] = []
    add = L.append

    add(f"# Backtest report — {NAMES.get(run['strategy'], run['strategy'])} (run #{run['id']})\n")
    add("> Past behaviour on historical data only — **not a promise of future profit** and not investment advice. "
        "Produced by a personal research tool; no orders were placed.\n")
    add(f"- **Period tested:** {run['start_date']} → {run['end_date']} (daily candles)")
    add(f"- **Unseen period (judge here):** {run['unseen_from']} → {run['end_date']} — the last 2 years")
    add("- **Universe:** the stocks in the local database today (Nifty 200 list) + NIFTY 50 and NIFTY BANK")
    excluded = (run.get("params") or {}).get("exclude_symbols") or []
    if excluded:
        add(f"- **Robustness check — stocks left out:** {', '.join(excluded)}")
    add("- **Data:** Yahoo Finance daily prices, split-adjusted by Yahoo; cases Yahoo missed are flagged in "
        "section 4\n")

    add("## 1. What the strategy does\n")
    add(RULES.get(run["strategy"], "(see the design spec)") + "\n")
    add("## 2. How every trade is executed (same for all strategies)\n")
    add("- Signals are decided **after the close** using only data up to that day; the trade is entered at the "
        "**next day's open** (no peeking ahead).")
    percent = money.get("risk_mode") == "percent"
    add("- **Size:** shares = risk per trade ÷ (entry − stop), capped by the per-position limit and the cash "
        "available. If cash can fund less than half the intended size, the trade is skipped."
        + (" Risk per trade is a percentage of equity (cash + open positions at the previous close)."
           if percent else ""))
    strategy_params = (run.get("params") or {}).get("strategy") or {}
    is_d = run["strategy"] == "price_action"
    is_a2 = run["strategy"] == "momentum_a2"
    trailing = run["strategy"] in TRAILING
    if trailing:
        add(f"- **No fixed target.** After each close the stop becomes max(previous stop, highest close since "
            f"entry − {plain(strategy_params.get('trail_atr'))} × ATR(14)); it never moves down and applies from "
            "the next day.")
        add("- **Exits, checked daily:** opening below the stop → sell at the open; low touches the stop → sell at "
            "the stop (\"trail stop\" once it has risen above the initial stop); after "
            f"{plain(strategy_params.get('time_stop_days'))} trading days → sell at the close."
            + (" **Rank exit:** on each weekly signal day, a held stock no longer in the top 30 by score "
               "(or closing below its 200-day average) is sold at the next open — recorded as \"stop gap\" if "
               "that open is already below the stop." if is_a2 else ""))
    elif is_d:
        add("- **Exits, checked daily:** opening below the stop → sell at the open; low touches the stop → sell at "
            "the stop; high touches entry + 1.5 × (entry − stop) → sell **half** there, then the rest gets a "
            "breakeven stop from the next day and trails below the lowest low of the last 5 days; **if the stop "
            "and the 1.5R level are both touched the same day it is counted as a loss**; after 30 trading days → "
            "sell the rest at the close. One trade row covers both halves (average exit price, both sells' "
            "charges).")
    else:
        add("- **Target** = entry + 2 × (entry − stop).")
        add("- **Exits, checked daily:** opening below the stop → sell at the open; low touches the stop → sell at "
            "the stop; high touches the target → sell at the target; **if both happen the same day it is counted "
            "as a loss** (daily candles can't show which came first); after 20 trading days → sell at the close.")
    add("- If closed losses in a day reach the daily loss limit, no new trades the next day.")
    if money.get("regime_filter"):
        add("- **Market regime filter:** no new long entries when NIFTY 50 closes at or below its 200-day average on "
            "the signal day; open positions keep their normal exits.")
    if money.get("max_charge_to_risk") is not None:
        add(f"- **Charge check:** a trade is skipped when its estimated round-trip charges + slippage (buy and sell "
            f"at the entry price) exceed {pct(money['max_charge_to_risk'])} of the rupees risked.")
    if money.get("max_drawdown_pause") is not None:
        add(f"- **Drawdown pause:** when equity falls {pct(money['max_drawdown_pause'])} or more below its peak, new "
            f"entries stop; they resume once at least {money.get('pause_min_days', 10)} trading days have passed "
            "and NIFTY 50 is above its 200-day average, and the peak then resets to that day's equity.")
    if money.get("dd_risk_scaling"):
        add("- **Drawdown risk scaling:** while equity is more than 10% below its all-time peak (this peak never "
            "resets), risk per trade is halved; full risk returns once equity is within 5% of that peak.")
    add("- **Costs:** Zerodha delivery charges (STT 0.1% each side, exchange, SEBI, stamp duty, GST, DP charge "
        "per sale) plus slippage on every buy and sell.\n")
    add("| Money rule | Value |\n|---|---|")
    add(f"| Starting capital | {rupees(money.get('capital'))} |")
    add(f"| Risk per trade | {pct(money.get('risk_pct')) + ' of equity' if percent else rupees(money.get('risk_per_trade'))} |")
    daily_pct = money.get("daily_loss_pct")
    add(f"| Daily loss stop | {pct(daily_pct) + ' of equity' if daily_pct else rupees(money.get('daily_loss_limit'))} |")
    add(f"| Max open positions | {plain(money.get('max_positions'))} |")
    add(f"| Max money in one stock | {rupees(money.get('max_position_value'))} |")
    add(f"| Market regime filter (NIFTY 50 vs 200-day avg) | {'On' if money.get('regime_filter') else 'Off'} |")
    charge = money.get("max_charge_to_risk")
    add(f"| Max charges ÷ risk | {'Off' if charge is None else pct(charge)} |")
    dd = money.get("max_drawdown_pause")
    cooldown = money.get("reentry_cooldown_days")
    if cooldown is None:
        cooldown = strategy_params.get("reentry_cooldown_days", 0)
    short = money.get("regime_filter_short")
    if short is None:
        short = strategy_params.get("regime_filter_short", False)
    add(f"| Short-term filter (NIFTY 50 above its 50- and 200-day avg) | {'On' if short else 'Off'} |")
    add(f"| Drawdown risk scaling | {'Half risk below −10% from the all-time peak, full within −5%' if money.get('dd_risk_scaling') else 'Off'} |")
    add(f"| Re-entry cooldown after a stop | {f'{cooldown} trading days' if cooldown else 'Off'} |")
    add(f"| Drawdown pause | {'Off' if dd is None else pct(dd) + ' below peak, min ' + plain(money.get('pause_min_days', 10)) + ' trading days'} |")
    if is_a2:
        add(f"| Initial stop (× ATR14) | {plain(strategy_params.get('stop_atr'))} |")
        add(f"| Trailing stop (× ATR14 below highest close) | {plain(strategy_params.get('trail_atr'))} |")
        add(f"| Candidates per week / keep if in top | {plain(strategy_params.get('top_n'))} / "
            f"{plain(strategy_params.get('keep_top'))} |")
        add(f"| Time stop (trading days) | {plain(strategy_params.get('time_stop_days'))} |")
    elif run["strategy"] == "breakout_c3":
        add(f"| Stop never closer than (× ATR14 below the signal close) | "
            f"{plain(strategy_params.get('stop_floor_atr'))} |")
        add(f"| Stop distance at entry (× ATR14) | {plain(strategy_params.get('entry_min_atr'))}–"
            f"{plain(strategy_params.get('entry_max_atr'))} |")
        add(f"| Trailing stop (× ATR14 below highest close) | {plain(strategy_params.get('trail_atr'))} |")
        add(f"| Time stop (trading days) | {plain(strategy_params.get('time_stop_days'))} |")
    elif run["strategy"] == "breakout_c2":
        add(f"| Stop distance at entry (× ATR14) | {plain(strategy_params.get('entry_min_atr'))}–"
            f"{plain(strategy_params.get('entry_max_atr'))} |")
        add(f"| Trailing stop (× ATR14 below highest close) | {plain(strategy_params.get('trail_atr'))} |")
        add(f"| Time stop (trading days) | {plain(strategy_params.get('time_stop_days'))} |")
    elif run["strategy"] == "pullback_b3":
        add(f"| Stop never closer than (× ATR14) | {plain(strategy_params.get('stop_atr'))} |")
        add(f"| Stop distance at entry (× ATR14) | {plain(strategy_params.get('entry_min_atr'))}–"
            f"{plain(strategy_params.get('entry_max_atr'))} |")
        add(f"| Relative strength lookback (days) | {plain(strategy_params.get('rs_days'))} |")
        add(f"| Trailing stop (× ATR14 below highest close) | {plain(strategy_params.get('trail_atr'))} |")
        add(f"| Time stop (trading days) | {plain(strategy_params.get('time_stop_days'))} |")
    elif run["strategy"] == "pullback_b2":
        add(f"| Stop never closer than (× ATR14) | {plain(strategy_params.get('stop_atr'))} |")
        add(f"| Stop distance at entry (× ATR14) | {plain(strategy_params.get('entry_min_atr'))}–"
            f"{plain(strategy_params.get('entry_max_atr'))} |")
        add(f"| Relative strength lookback (days) | {plain(strategy_params.get('rs_days'))} |")
    if is_d:
        add(f"| Partial profit at (× risk) | {plain(strategy_params.get('partial_r'))} |")
        add(f"| Time stop (trading days) | {plain(strategy_params.get('time_stop_days'))} |")
    elif not trailing:  # trailing strategies list their time stop above and have no target
        add(f"| Target (× risk) | {plain(money.get('reward_multiple'))} |")
        add(f"| Time stop (trading days) | {plain(money.get('time_stop_days'))} |")
    add("")

    add("## 3. Results\n")
    add("| | Earlier years | Unseen years | Whole run |\n|---|---|---|---|")
    for label, key, fmt in ROWS:
        add(f"| {label} | {fmt((s.get('earlier') or {}).get(key))} | {fmt((s.get('unseen') or {}).get(key))} | "
            f"{fmt((s.get('whole') or {}).get(key))} |")
    add(f"| NIFTY 50 buy & hold — return | | {pct((bench.get('unseen') or {}).get('return_pct'))} | "
        f"{pct((bench.get('whole') or {}).get('return_pct'))} |")
    add(f"| NIFTY 50 buy & hold — biggest fall | | {pct((bench.get('unseen') or {}).get('max_drawdown_pct'))} | "
        f"{pct((bench.get('whole') or {}).get('max_drawdown_pct'))} |\n")
    add("*R = profit or loss divided by the rupees risked on that trade ((entry − stop) × shares). "
        + ("−1R = stop hit before the partial; half sold at 1.5R then the rest out at breakeven ≈ +0.75R; all "
           "before charges and slippage.*\n" if is_d else
           "−1R = the initial stop hit; there is no fixed target, so winners can run past +2R.*\n" if trailing else
           "+2R = target hit, −1R = stop hit, before charges and slippage.*\n"))

    dd = s.get("drawdown") or {}
    if dd.get("peak_date"):
        add("### Biggest fall\n")
        add(f"- **{pct(dd['max_drawdown_pct'])}** from the peak on {dd['peak_date']} to the low on "
            f"{dd['trough_date']}; recovered on {dd['recovery_date'] or 'not recovered by the end of the test'}.")
        add(f"- Longest time below a previous peak: **{dd['longest_underwater_months']:.1f} months**.")
        if s.get("half_risk_days"):
            add(f"- Days traded at half risk (drawdown risk scaling): **{s['half_risk_days']}**.")
        add("")

    add("### Minimum-evidence checklist (not a promise of profit)\n")
    L.extend(f"- {'n/a — not enough trades to judge:' if c.get('na') else ('✅' if c['passed'] else '❌')} "
             f"{c['label']}" for c in s.get("checklist", []))
    add("")
    streaks = (s.get("loss_streaks") or {}).get("whole") or {}
    if streaks.get("alerts"):
        add("### ⚠ Two losses in a row\n")
        add(f"2 losses in a row happened {streaks['alerts']} times. The {streaks['after_trades']} trades taken "
            f"right after averaged {num(streaks['after_avg_r'])}R (win rate {pct(streaks['after_win_rate'])}). "
            "Trading was not paused — this is a notice only, so you can judge whether pausing would have "
            "helped.\n")

    gross = sum(float(t["pnl"]) + float(t["charges"]) for t in trades)
    charges = sum(float(t["charges"]) for t in trades)
    net = sum(float(t["pnl"]) for t in trades)
    risks = [int(t["shares"]) * (float(t["entry_price"]) - float(t["stop"])) for t in trades]
    add("### Where the money went: gross vs charges\n")
    add(f"- Profit **before** charges across all {len(trades)} trades: **{rupees(gross)}**")
    add(f"- Charges paid: **{rupees(charges)}** (average {rupees(charges / len(trades) if trades else None)} "
        "per trade)")
    add(f"- Profit **after** charges: **{rupees(net)}**")
    if trades and sum(risks) > 0:
        avg_risk = sum(risks) / len(risks)
        add(f"- Average rupees actually risked per trade: **{rupees(avg_risk)}**, so charges alone cost about "
            f"**{charges / len(trades) / avg_risk:.2f}R** per trade.")
    add("")
    _edge_sections(add, L, trades, s)

    add("### By year\n\n| Year | Return | Biggest fall | Trades |\n|---|---|---|---|")
    L.extend(f"| {y['year']} | {pct(y['return_pct'])} | {pct(y['max_drawdown_pct'])} | {y['trades']} |"
             for y in s.get("years", []))
    add("")
    add("### How trades ended\n\n| Exit | Count |\n|---|---|")
    L.extend(f"| {k.replace('_', ' ')} | {v} |" for k, v in Counter(t["exit_reason"] for t in trades).most_common())
    add("")
    add("### Signals that were skipped\n\n| Reason | Count |\n|---|---|")
    L.extend(f"| {k.replace('_', ' ')} | {v} |"
             for k, v in sorted((s.get("skips") or {}).items(), key=lambda kv: -kv[1]))
    add("\n*no slot = all positions already open; no cash = cash couldn't fund at least half the intended size; "
        "size zero = one share would exceed the per-position limit; data problem = a likely unadjusted split/bonus "
        "in that stock within the previous ~300 days"
        + "".join(f"; {k.replace('_', ' ')} = {v}" for k, v in SKIP_NOTES.items() if k in (s.get("skips") or {}))
        + ".*\n")

    if s.get("index_signals"):
        add("### NIFTY / BANKNIFTY signals — **not for trading, research only** (index signal quality in R, not "
            "option profit)\n")
        add("| Index | Idea | Period | Signals | Win rate | Avg R |\n|---|---|---|---|---|---|")
        L.extend(f"| {r['symbol']} | {'Bullish (CE)' if r['direction'] == 'long' else 'Bearish (PE)'} | "
                 f"{r['period']} | {r['signals']} | {pct(r['win_rate'])} | {num(r['avg_r'])} |"
                 for r in s["index_signals"])
        add("")

    add("## 4. Known data problems and caveats\n")
    L.extend(f"- {w}" for w in s.get("warnings", []))
    flags = s.get("data_flags") or []
    if flags:
        add("\nOne-day price jumps over 35% found by the full-history data check. **Likely unadjusted "
            "split/bonus**: no entries for ~300 days after it, and an open trade was closed at its last good price. "
            "**Large real move**: kept exactly as it happened.\n")
        add("| Symbol | Date | Type | Detail |\n|---|---|---|---|")
        L.extend(f"| {f['symbol']} | {f['date']} | "
                 f"{'Likely unadjusted split/bonus' if f.get('kind') == 'adjustment' else 'Large real move'} | "
                 f"{f['detail']} |" for f in flags)
    odd = [t for t in trades if abs(float(t["r_multiple"])) > 3]
    if odd:
        add("\nTrades with results beyond ±3R (gaps, tiny stop distances, or data errors):\n")
        add("| Symbol | Entry | Exit date | Buy | Sell | Exit | ₹ | R |\n|---|---|---|---|---|---|---|---|")
        L.extend(f"| {t['symbol']} | {t['entry_date']} | {t['exit_date']} | {float(t['entry_price']):.2f} | "
                 f"{float(t['exit_price']):.2f} | {t['exit_reason']} | {rupees(t['pnl'])} | "
                 f"{float(t['r_multiple']):.1f} |" for t in odd)
    add("")

    add("## 5. Questions to study (paste this report into Claude or another AI)\n")
    L.extend(f"{i}. {q}" for i, q in enumerate(QUESTIONS, start=1))
    add("")
    add(f"Files: `{file_stem(run)}-trades.csv` (every trade with its reason) and `{file_stem(run)}-equity.csv` "
        "(daily account value vs NIFTY 50).")
    return "\n".join(L) + "\n"


def _edge_sections(add, L: List[str], trades: List[dict], s: dict) -> None:
    """R before/after charges, stop distance, market regime, position size and drawdown pauses."""
    periods = (("Earlier years", [t for t in trades if t["period"] == "earlier"]),
               ("Unseen years", [t for t in trades if t["period"] == "unseen"]), ("Whole run", trades))
    add("### R per trade: before charges, charges, after charges\n")
    add("| | Trades | Gross R | Charges R | Net R |\n|---|---|---|---|---|")
    for label, group in periods:
        r = analysis.r_split(group)
        add(f"| {label} | {r['trades']} | {num(r['gross_r'])} | {num(r['charges_r'])} | {num(r['net_r'])} |")
    add("")

    add("### By stop distance (measured from the actual entry price)\n")
    add("| Stop distance | Period | Trades | Avg position | Avg ₹ risked | Win rate | Gross R | Charges R | Net R |\n"
        "|---|---|---|---|---|---|---|---|---|")
    L.extend(f"| {r['bucket']} | {r['period']} | {r['trades']} | {rupees(r['avg_position_value'])} | "
             f"{rupees(r['avg_risk'])} | {pct(r['win_rate'])} | {num(r['gross_r'])} | {num(r['charges_r'])} | "
             f"{num(r['net_r'])} |" for r in analysis.stop_distance_table(trades))
    add("")

    add("### By NIFTY 50 vs its 50-day average on the signal day\n")
    add("| Period | NIFTY 50 | Trades | Win rate | Avg R | ₹ P&L |\n|---|---|---|---|---|---|")
    L.extend(f"| {r['period']} | {r['regime']} | {r['trades']} | {pct(r['win_rate'])} | {num(r['avg_r'])} | "
             f"{rupees(r['pnl'])} |" for r in analysis.sma50_table(trades))
    add("")

    add("### By market regime on the signal day (NIFTY 50 vs its 200-day average)\n")
    add("| Period | NIFTY 50 | Trades | Win rate | Avg R | ₹ P&L |\n|---|---|---|---|---|---|")
    L.extend(f"| {r['period']} | {r['regime']} | {r['trades']} | {pct(r['win_rate'])} | {num(r['avg_r'])} | "
             f"{rupees(r['pnl'])} |" for r in analysis.regime_table(trades))
    add("\n*\"unknown\" = trades from runs made before this was recorded.*\n")

    size = analysis.sizing(trades)
    add("### By entry gap (next day's open vs the signal-day close)\n")
    add("| Entry gap | Period | Trades | Win rate | Gross R | Net R |\n|---|---|---|---|---|---|")
    L.extend(f"| {r['bucket']} | {r['period']} | {r['trades']} | {pct(r['win_rate'])} | {num(r['gross_r'])} | "
             f"{num(r['net_r'])} |" for r in analysis.entry_gap_table(trades))
    add("")

    spread = s.get("charge_check")
    if spread:
        add("### Estimated charges ÷ risk (round-trip charges + slippage ÷ rupees risked, at the actual entry)\n")
        add("| | Count | Min | Median | Max |\n|---|---|---|---|---|")
        for label, key in (("Taken", "taken"), ("Skipped by the charge check", "skipped")):
            d = spread.get(key) or {}
            add(f"| {label} | {d.get('count', 0)} | {pct(d.get('min'))} | {pct(d.get('median'))} | "
                f"{pct(d.get('max'))} |")
        add("")

    add("### Max favourable excursion by exit (best high while held, in R — how much profit was given back)\n")
    add("| Exit | Trades | Avg MFE (R) | Avg final R |\n|---|---|---|---|")
    L.extend(f"| {r['exit_reason'].replace('_', ' ')} | {r['trades']} | {num(r['avg_mfe_r'])} | {num(r['avg_r'])} |"
             for r in analysis.mfe_by_exit(trades))
    add("")

    add("### By stock volatility at entry (ATR(14) ÷ close on the signal day)\n")
    add("| Volatility | Period | Trades | Win rate | Gross R | Net R |\n|---|---|---|---|---|---|")
    L.extend(f"| {r['bucket']} | {r['period']} | {r['trades']} | {pct(r['win_rate'])} | {num(r['gross_r'])} | "
             f"{num(r['net_r'])} |" for r in analysis.volatility_table(trades))
    add("")

    top = analysis.top_stocks(trades)
    add("### Most-traded stocks\n")
    add("| Stock | Trades | Win rate | Net R per trade | ₹ P&L |\n|---|---|---|---|---|")
    L.extend(f"| {r['symbol']} | {r['trades']} | {pct(r['win_rate'])} | {num(r['net_r'])} | {rupees(r['pnl'])} |"
             for r in top["rows"])
    add(f"\nThe 5 stocks that made the most money made {rupees(top['top5_pnl'])} of the {rupees(top['total_pnl'])} "
        f"total P&L ({pct(top['top5_share'])}).\n")

    add("### Position size\n")
    add(f"- Average position value: **{rupees(size['avg_position_value'])}**")
    add(f"- Trades cut below the intended risk: **{pct(size['pct_cut'])}** ({size['cut_by_cap']} by the "
        f"per-stock cap, {size['cut_by_cash']} by the cash available)\n")

    pauses = s.get("drawdown_pauses") or []
    if pauses:
        skipped = (s.get("skips") or {}).get("drawdown_pause", 0)
        days = sum(p.get("days_paused") or 0 for p in pauses)
        add("### Drawdown pauses\n")
        add(f"{len(pauses)} pause(s), {days} trading days paused in total, {skipped} signals skipped.\n")
        add("| Paused at close of | Equity then | Resumed at close of | Equity then | Trading days paused |\n"
            "|---|---|---|---|---|")
        L.extend(f"| {p['start_date']} | {rupees(p['equity_at_pause'])} | {p.get('resume_date') or 'still paused'} "
                 f"| {rupees(p.get('equity_at_resume'))} | {plain(p.get('days_paused'))} |" for p in pauses)
        add("")


def _csv(header: List[str], rows, cell: Callable) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([cell(r, h) for h in header])
    return buf.getvalue()


TRADE_COLUMNS = ["symbol", "signal_date", "entry_date", "entry_price", "shares", "stop", "target", "exit_date",
                 "exit_price", "exit_reason", "charges", "pnl", "r_multiple", "period", "reason"]


FACT_COLUMNS = ["mfe_r", "atr_pct"]  # from each trade's recorded facts (blank for older runs)


def trades_csv(run: dict) -> str:
    return _csv(TRADE_COLUMNS + FACT_COLUMNS, run["trades"],
                lambda t, h: t[h] if h in t else (t.get("facts") or {}).get(h))


EQUITY_COLUMNS = {"date": "trade_date", "cash": "cash", "invested_value": "invested_value", "equity": "equity",
                  "nifty50_buy_and_hold": "benchmark_equity"}


def equity_csv(run: dict) -> str:
    return _csv(list(EQUITY_COLUMNS), run["equity"], lambda p, h: p[EQUITY_COLUMNS[h]])
