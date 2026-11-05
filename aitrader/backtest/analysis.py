"""Breakdowns of a finished run's trade rows (as stored), and the multi-run comparison table."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

PERIODS = ("earlier", "unseen")
STOP_BUCKETS = ((0.02, "<2%"), (0.03, "2–3%"), (0.04, "3–4%"), (0.06, "4–6%"), (0.10, "6–10%"), (float("inf"), ">10%"))


def _f(t: dict, k: str) -> float:
    return float(t[k])


def risk_of(t: dict) -> float:
    """Rupees actually risked: (entry − stop) × shares, using the real entry price."""
    return int(t["shares"]) * (_f(t, "entry_price") - _f(t, "stop"))


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def r_split(trades: Sequence[dict]) -> dict:
    """Average R per trade before charges, the charges in R, and after charges (net = the trade's R)."""
    ok = [t for t in trades if risk_of(t) > 0]
    return {"trades": len(trades),
            "gross_r": _mean([(_f(t, "pnl") + _f(t, "charges")) / risk_of(t) for t in ok]),
            "charges_r": _mean([_f(t, "charges") / risk_of(t) for t in ok]),
            "net_r": _mean([_f(t, "r_multiple") for t in trades])}


def stop_bucket(entry: float, stop: float) -> str:
    dist = (entry - stop) / entry
    return next(label for upper, label in STOP_BUCKETS if dist < upper)


def _win_rate(trades: Sequence[dict]) -> Optional[float]:
    return sum(1 for t in trades if _f(t, "pnl") > 0) / len(trades) if trades else None


def stop_distance_table(trades: Sequence[dict]) -> List[dict]:
    out = []
    for _, label in STOP_BUCKETS:
        for period in PERIODS:
            group = [t for t in trades if t["period"] == period
                     and stop_bucket(_f(t, "entry_price"), _f(t, "stop")) == label]
            if not group:
                continue
            out.append({"bucket": label, "period": period, "trades": len(group),
                        "avg_position_value": _mean([_f(t, "entry_price") * int(t["shares"]) for t in group]),
                        "avg_risk": _mean([risk_of(t) for t in group]), "win_rate": _win_rate(group),
                        **{k: v for k, v in r_split(group).items() if k != "trades"}})
    return out


GAP_BUCKETS = ((0.0, "<0%"), (0.01, "0–1%"), (0.03, "1–3%"), (0.05, "3–5%"), (float("inf"), ">5%"))


def gap_bucket(entry_open: float, signal_close: float) -> str:
    gap = entry_open / signal_close - 1
    return next(label for upper, label in GAP_BUCKETS if gap < upper)


def _gap_label(t: dict) -> str:
    facts = t.get("facts") or {}
    if facts.get("entry_open") is None or not facts.get("signal_close"):
        return "unknown"
    return gap_bucket(float(facts["entry_open"]), float(facts["signal_close"]))


def entry_gap_table(trades: Sequence[dict]) -> List[dict]:
    """Trades by the entry-day gap: next day's open ÷ the signal-day close − 1."""
    out = []
    for label in [b for _, b in GAP_BUCKETS] + ["unknown"]:
        for period in PERIODS:
            group = [t for t in trades if t["period"] == period and _gap_label(t) == label]
            if group:
                r = r_split(group)
                out.append({"bucket": label, "period": period, "trades": len(group), "win_rate": _win_rate(group),
                            "gross_r": r["gross_r"], "net_r": r["net_r"]})
    return out


VOL_BUCKETS = ((0.02, "<2%"), (0.03, "2–3%"), (0.04, "3–4%"), (0.06, "4–6%"), (float("inf"), ">6%"))


def vol_bucket(atr_pct: float) -> str:
    return next(label for upper, label in VOL_BUCKETS if atr_pct < upper)


def _facts(t: dict) -> dict:
    return t.get("facts") or {}


def volatility_table(trades: Sequence[dict]) -> List[dict]:
    """Trades by the stock's ATR(14) ÷ close on the signal day."""
    def label(t):
        v = _facts(t).get("atr_pct")
        return "unknown" if v is None else vol_bucket(float(v))
    out = []
    for bucket in [b for _, b in VOL_BUCKETS] + ["unknown"]:
        for period in PERIODS:
            group = [t for t in trades if t["period"] == period and label(t) == bucket]
            if group:
                r = r_split(group)
                out.append({"bucket": bucket, "period": period, "trades": len(group), "win_rate": _win_rate(group),
                            "gross_r": r["gross_r"], "net_r": r["net_r"]})
    return out


def top_stocks(trades: Sequence[dict], n: int = 10) -> dict:
    """The n most-traded stocks, and how much of the total ₹ P&L the five biggest contributors made."""
    by: dict = {}
    for t in trades:
        by.setdefault(t["symbol"], []).append(t)
    rows = [{"symbol": sym, "trades": len(g), "win_rate": _win_rate(g),
             "net_r": _mean([_f(t, "r_multiple") for t in g]), "pnl": sum(_f(t, "pnl") for t in g)}
            for sym, g in by.items()]
    total = sum(r["pnl"] for r in rows)
    top5 = sum(sorted((r["pnl"] for r in rows), reverse=True)[:5])
    return {"rows": sorted(rows, key=lambda r: (-r["trades"], r["symbol"]))[:n], "total_pnl": total,
            "top5_pnl": top5, "top5_share": top5 / total if total > 0 else None}


def bootstrap_years(r_multiples: Sequence[float], trades_per_year: int, runs: int = 10_000,
                    seed: int = 19) -> dict:
    """Resample past trade results (in R) into many imaginary years of `trades_per_year` trades each: the spread
    of a year's total R, the odds of a losing year, and the typical worst losing streak inside a year."""
    import random

    rs = [float(r) for r in r_multiples]
    if not rs or trades_per_year < 1:
        return {"runs": 0}
    rng = random.Random(seed)
    totals, streaks = [], []
    for _ in range(runs):
        year = [rng.choice(rs) for _ in range(trades_per_year)]
        totals.append(sum(year))
        worst = cur = 0
        for r in year:
            cur = cur + 1 if r <= 0 else 0
            worst = max(worst, cur)
        streaks.append(worst)
    totals.sort()
    streaks.sort()

    def q(xs, p):
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    return {"runs": runs, "seed": seed, "trades_per_year": trades_per_year, "p5_r": q(totals, 0.05),
            "median_r": q(totals, 0.5), "p95_r": q(totals, 0.95),
            "losing_year_odds": sum(1 for t in totals if t <= 0) / runs,
            "median_worst_streak": q(streaks, 0.5), "p95_worst_streak": q(streaks, 0.95)}


def by_label_table(trades: Sequence[dict], key: str) -> List[dict]:
    """Trades grouped by a recorded fact (e.g. "group", "sector"): trades, net R, ₹ P&L, share of the total P&L."""
    by: dict = {}
    for t in trades:
        by.setdefault(_facts(t).get(key) or "none", []).append(t)
    total = sum(_f(t, "pnl") for t in trades)
    rows = [{"label": label, "trades": len(g), "net_r": _mean([_f(t, "r_multiple") for t in g]),
             "pnl": sum(_f(t, "pnl") for t in g)} for label, g in by.items()]
    for r in rows:
        r["share"] = r["pnl"] / total if total > 0 else None
    return sorted(rows, key=lambda r: -r["pnl"])


def big_winner_share(trades: Sequence[dict], threshold_r: float = 5.0) -> Optional[float]:
    """Share of the total ₹ P&L made by trades above threshold_r (None when the total isn't positive)."""
    total = sum(_f(t, "pnl") for t in trades)
    big = sum(_f(t, "pnl") for t in trades if _f(t, "r_multiple") > threshold_r)
    return big / total if total > 0 else None


def quarter_table(trades: Sequence[dict], period: str) -> List[dict]:
    """Trades of one period by the calendar quarter they closed in."""
    by: dict = {}
    for t in trades:
        if t["period"] != period:
            continue
        year, month = str(t["exit_date"])[:4], int(str(t["exit_date"])[5:7])
        by.setdefault(f"{year}-Q{(month - 1) // 3 + 1}", []).append(t)
    return [{"quarter": q, "trades": len(g), "net_r": _mean([_f(t, "r_multiple") for t in g]),
             "pnl": sum(_f(t, "pnl") for t in g)} for q, g in sorted(by.items())]


def mfe_by_exit(trades: Sequence[dict]) -> List[dict]:
    """Average max favourable excursion (best high while held, in R) and final R, by exit reason."""
    out = []
    for reason in sorted({t["exit_reason"] for t in trades}):
        group = [t for t in trades if t["exit_reason"] == reason]
        mfes = [float(_facts(t)["mfe_r"]) for t in group if _facts(t).get("mfe_r") is not None]
        out.append({"exit_reason": reason, "trades": len(group), "with_mfe": len(mfes), "avg_mfe_r": _mean(mfes),
                    "avg_r": _mean([_f(t, "r_multiple") for t in group])})
    return sorted(out, key=lambda r: -r["trades"])


def _regime(t: dict, key: str = "regime_on") -> str:
    on = (t.get("facts") or {}).get(key)
    return "unknown" if on is None else ("above" if on else "below")


def sma50_table(trades: Sequence[dict]) -> List[dict]:
    """Trades by NIFTY 50 vs its 50-day average on the signal day."""
    return regime_table(trades, "nifty_above_sma50")


def regime_table(trades: Sequence[dict], key: str = "regime_on") -> List[dict]:
    out = []
    for period in PERIODS:
        for regime in ("above", "below", "unknown"):
            group = [t for t in trades if t["period"] == period and _regime(t, key) == regime]
            if group:
                out.append({"period": period, "regime": regime, "trades": len(group), "win_rate": _win_rate(group),
                            "avg_r": _mean([_f(t, "r_multiple") for t in group]),
                            "pnl": sum(_f(t, "pnl") for t in group)})
    return out


def sizing(trades: Sequence[dict]) -> dict:
    cuts = [(t.get("facts") or {}).get("size_cut") for t in trades]
    return {"avg_position_value": _mean([_f(t, "entry_price") * int(t["shares"]) for t in trades]),
            "pct_cut": sum(1 for c in cuts if c) / len(trades) if trades else None,
            "cut_by_cap": sum(1 for c in cuts if c == "cap"), "cut_by_cash": sum(1 for c in cuts if c == "cash")}


# --- comparison ------------------------------------------------------------------------------------------------

def _pct(v) -> str:
    return "—" if v is None else f"{float(v) * 100:.1f}%"


def _num(v) -> str:
    return "—" if v is None else f"{float(v):.2f}"


def _rupees(v) -> str:
    return "—" if v is None else f"{'-' if float(v) < 0 else ''}₹{abs(float(v)):,.0f}"


COLUMNS: Tuple[Tuple[str, str, object], ...] = (
    ("Return", "return_pct", _pct), ("CAGR", "cagr", _pct), ("Max drawdown", "max_drawdown_pct", _pct),
    ("Longest underwater (months)", "longest_underwater_months", lambda v: "—" if v is None else f"{float(v):.1f}"),
    ("Worst month", "worst_month", _pct), ("Trades", "trades", lambda v: "—" if v is None else str(v)),
    ("% days invested", "pct_days_invested", _pct),
    ("Win rate", "win_rate", _pct), ("Net expectancy (R)", "expectancy_r", _num),
    ("Profit factor", "profit_factor", _num), ("Charges", "total_charges", _rupees),
    ("Charges % of gross profit", "charges_pct_of_gross", _pct), ("Top-5 share of profit", "top5_share", _pct),
)


def checklist_result(summary: dict) -> str:
    items = summary.get("checklist") or []
    if not items:
        return "—"
    na = sum(1 for c in items if c.get("na"))
    return f"{sum(1 for c in items if c['passed'])}/{len(items)}" + (f" ({na} n/a)" if na else "")


def comparison_markdown(rows: Sequence[Tuple[str, dict]], title: str = "Comparison") -> str:
    """One row per run, separate tables for the earlier and the unseen years. rows: (label, run with summary)."""
    head = "| Run | " + " | ".join(c[0] for c in COLUMNS) + " | Checklist |"
    rule = "|" + "---|" * (len(COLUMNS) + 2)
    out = [f"# {title}\n"]
    for period, heading in (("earlier", "Earlier years"), ("unseen", "Unseen years (judge here)")):
        out += [f"## {heading}\n", head, rule]
        for label, run in rows:
            s = run["summary"]
            p = s.get(period) or {}
            out.append(f"| {label} | " + " | ".join(fmt(p.get(key)) for _, key, fmt in COLUMNS)
                       + f" | {checklist_result(s)} |")
        out.append("")
    out.append("*Checklist = minimum-evidence checks passed for the whole run (not a promise of profit). Past "
               "behaviour on historical data only — not a promise of future profit and not investment advice.*")
    return "\n".join(out) + "\n"


MIN_TRADES_FOR_BEST = 30
BEST_RULE = (f"at least {MIN_TRADES_FOR_BEST} trades; most minimum-evidence checks passed; then highest whole-run "
             "net expectancy")


def best_run(rows: Sequence[Tuple[str, dict]]) -> Optional[Tuple[str, dict]]:
    """Pick one run per strategy family by BEST_RULE (falls back to all runs when none has enough trades)."""
    def trades(row):
        return (row[1]["summary"].get("whole") or {}).get("trades") or 0

    def key(row):
        s = row[1]["summary"]
        e = (s.get("whole") or {}).get("expectancy_r")
        return (sum(1 for c in s.get("checklist") or [] if c["passed"]), float("-inf") if e is None else e)

    pool = [r for r in rows if trades(r) >= MIN_TRADES_FOR_BEST] or list(rows)
    return max(pool, key=key) if pool else None
