"""Study report for one finished backtest run: Markdown for reading (or pasting into an AI) plus CSVs."""
from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Callable, List

NAMES = {"momentum": "A — Momentum", "pullback": "B — Trend pullback", "breakout": "C — Breakout",
         "price_action": "D — Price action"}

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
}

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
    add("- **Data:** Yahoo Finance daily prices, split-adjusted by Yahoo; cases Yahoo missed are flagged in "
        "section 4\n")

    add("## 1. What the strategy does\n")
    add(RULES.get(run["strategy"], "(see the design spec)") + "\n")
    add("## 2. How every trade is executed (same for all strategies)\n")
    add("- Signals are decided **after the close** using only data up to that day; the trade is entered at the "
        "**next day's open** (no peeking ahead).")
    add("- **Size:** shares = risk per trade ÷ (entry − stop), capped by the per-position limit and the cash "
        "available. If cash can fund less than half the intended size, the trade is skipped.")
    if run["strategy"] == "price_action":
        add("- **D's exits replace the fixed 2R target:** half at entry + 1.5 × (entry − stop), then a "
            "breakeven stop from the next day and a trailing stop for the rest; 30-day limit. One trade row "
            "covers both halves (average exit price, both sells' charges).")
    else:
        add("- **Target** = entry + 2 × (entry − stop).")
    add("- **Exits, checked daily:** opening below the stop → sell at the open; low touches the stop → sell at the "
        "stop; high touches the target → sell at the target; **if both happen the same day it is counted as a "
        "loss** (daily candles can't show which came first); after 20 trading days → sell at the close.")
    add("- If closed losses in a day reach the daily loss limit, no new trades the next day.")
    add("- **Costs:** Zerodha delivery charges (STT 0.1% each side, exchange, SEBI, stamp duty, GST, DP charge "
        "per sale) plus slippage on every buy and sell.\n")
    add("| Money rule | Value |\n|---|---|")
    for label, key, fmt in (("Starting capital", "capital", rupees), ("Risk per trade", "risk_per_trade", rupees),
                            ("Daily loss stop", "daily_loss_limit", rupees),
                            ("Max open positions", "max_positions", plain),
                            ("Max money in one stock", "max_position_value", rupees),
                            ("Target (× risk)", "reward_multiple", plain),
                            ("Time stop (trading days)", "time_stop_days", plain)):
        add(f"| {label} | {fmt(money.get(key))} |")
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
        "+2R = target hit, −1R = stop hit, before charges and slippage.*\n")

    add("### Minimum-evidence checklist (not a promise of profit)\n")
    L.extend(f"- {'✅' if c['passed'] else '❌'} {c['label']}" for c in s.get("checklist", []))
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
        "in that stock within the previous ~300 days.*\n")

    if s.get("index_signals"):
        add("### NIFTY / BANKNIFTY signals — index signal quality in R, **not option profit**\n")
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


def _csv(header: List[str], rows, cell: Callable) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([cell(r, h) for h in header])
    return buf.getvalue()


TRADE_COLUMNS = ["symbol", "signal_date", "entry_date", "entry_price", "shares", "stop", "target", "exit_date",
                 "exit_price", "exit_reason", "charges", "pnl", "r_multiple", "period", "reason"]


def trades_csv(run: dict) -> str:
    return _csv(TRADE_COLUMNS, run["trades"], lambda t, h: t[h])


EQUITY_COLUMNS = {"date": "trade_date", "cash": "cash", "invested_value": "invested_value", "equity": "equity",
                  "nifty50_buy_and_hold": "benchmark_equity"}


def equity_csv(run: dict) -> str:
    return _csv(list(EQUITY_COLUMNS), run["equity"], lambda p, h: p[EQUITY_COLUMNS[h]])
