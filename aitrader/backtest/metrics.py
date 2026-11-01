from __future__ import annotations

import math
from datetime import date
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from aitrader.backtest.types import EquityPoint, MoneyRules, Trade

WARNINGS = [
    "Survivorship bias: today's Nifty 200 list is applied to past years, so stocks that later dropped out are "
    "missing and results look better than reality.",
    "NIFTY/BANKNIFTY results are index signal quality in R — not option profit.",
    "Benchmark is the NIFTY 50 price index — it excludes dividends.",
    "These are past results only — not a promise of future profit.",
]


def max_drawdown(values: Sequence[float]) -> Tuple[float, float]:
    peak, worst_pct, worst_amt = None, 0.0, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        if peak > 0 and (peak - v) / peak > worst_pct:
            worst_pct, worst_amt = (peak - v) / peak, peak - v
    return worst_pct, worst_amt


def cagr(start_value: float, end_value: float, start: date, end: date) -> Optional[float]:
    days = (end - start).days
    if days <= 0 or start_value <= 0 or end_value <= 0:
        return None
    return (end_value / start_value) ** (365.25 / days) - 1


def longest_losing_streak(pnls: Sequence[float]) -> int:
    best = run = 0
    for p in pnls:
        run = run + 1 if p <= 0 else 0
        best = max(best, run)
    return best


def worst_month(points: Sequence[EquityPoint], start_value: float) -> Optional[float]:
    month_end = {}
    for p in points:
        month_end[(p.day.year, p.day.month)] = p.equity
    prev, worst = start_value, None
    for key in sorted(month_end):
        r = month_end[key] / prev - 1
        worst = r if worst is None else min(worst, r)
        prev = month_end[key]
    return worst


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def trade_stats(trades: Sequence[Trade]) -> dict:
    ordered = sorted(trades, key=lambda t: (t.exit_date, t.entry_date, t.symbol))
    wins = [t for t in ordered if t.pnl > 0]
    losses = [t for t in ordered if t.pnl <= 0]
    gross_loss = -sum(t.pnl for t in losses)
    charges = sum(t.charges for t in ordered)
    gross_before = sum(t.pnl + t.charges for t in ordered)
    return {
        "trades": len(ordered),
        "win_rate": len(wins) / len(ordered) if ordered else None,
        "avg_win": _mean([t.pnl for t in wins]),
        "avg_loss": _mean([t.pnl for t in losses]),
        "avg_win_r": _mean([t.r_multiple for t in wins]),
        "avg_loss_r": _mean([t.r_multiple for t in losses]),
        "expectancy_r": _mean([t.r_multiple for t in ordered]),
        "profit_factor": sum(t.pnl for t in wins) / gross_loss if gross_loss > 0 else None,
        "total_charges": charges,
        "charges_pct_of_gross": charges / gross_before if gross_before > 0 else None,
        "longest_losing_streak": longest_losing_streak([t.pnl for t in ordered]),
    }


def period_summary(points: Sequence[EquityPoint], trades: Sequence[Trade], start_value: float) -> Optional[dict]:
    if not points:
        return None
    end_value = points[-1].equity
    dd_pct, dd_amt = max_drawdown([start_value] + [p.equity for p in points])
    return {
        "start_value": start_value,
        "end_value": end_value,
        "return_pct": end_value / start_value - 1,
        "cagr": cagr(start_value, end_value, points[0].day, points[-1].day),
        "max_drawdown_pct": dd_pct,
        "max_drawdown_amount": dd_amt,
        "worst_month": worst_month(points, start_value),
        "pct_days_invested": sum(1 for p in points if p.invested_value > 0) / len(points),
        **trade_stats(trades),
    }


def benchmark_curve(close: pd.Series, days, capital: float) -> List[float]:
    s = close.reindex(days).ffill().bfill()
    first = float(s.iloc[0])
    return [capital * float(v) / first for v in s]


def _bench(values: Sequence[float], start_value: float) -> Optional[dict]:
    if not values:
        return None
    return {"return_pct": values[-1] / start_value - 1,
            "max_drawdown_pct": max_drawdown([start_value, *values])[0]}


def yearly(points: Sequence[EquityPoint], trades: Sequence[Trade], start_value: float) -> List[dict]:
    out, prev = [], start_value
    for year in sorted({p.day.year for p in points}):
        mine = [p for p in points if p.day.year == year]
        out.append({"year": year, "return_pct": mine[-1].equity / prev - 1,
                    "max_drawdown_pct": max_drawdown([prev] + [p.equity for p in mine])[0],
                    "trades": sum(1 for t in trades if t.entry_date.year == year)})
        prev = mine[-1].equity
    return out


def evidence_checklist(earlier, unseen, whole, bench_unseen) -> List[dict]:
    def positive(p):
        return p is not None and p.get("expectancy_r") is not None and p["expectancy_r"] > 0

    items = [
        ("unseen_trades", "At least 30 trades in the unseen period", unseen is not None and unseen["trades"] >= 30),
        ("positive_expectancy", "Positive expectancy after charges in both periods",
         positive(earlier) and positive(unseen)),
        ("drawdown", "Biggest fall at most 20% over the whole run",
         whole is not None and whole["max_drawdown_pct"] <= 0.20),
        ("beats_benchmark", "Unseen period beats NIFTY 50 on return or on biggest fall",
         unseen is not None and bench_unseen is not None
         and (unseen["return_pct"] > bench_unseen["return_pct"]
              or unseen["max_drawdown_pct"] < bench_unseen["max_drawdown_pct"])),
    ]
    return [{"key": k, "label": label, "passed": bool(ok)} for k, label, ok in items]


def loss_streak_alerts(trades: Sequence[Trade]) -> dict:
    """"Two losses in a row" notices (never a pause): how many happened, and how the trades taken right after
    did — entered after the second loss closed, up to and including the next winning trade (by exit order)."""
    ordered = sorted(trades, key=lambda t: (t.exit_date, t.entry_date, t.symbol))
    alerts, streak, after = 0, 0, set()
    for i, t in enumerate(ordered):
        streak = streak + 1 if t.pnl <= 0 else 0
        if streak != 2:
            continue
        alerts += 1
        for j in range(i + 1, len(ordered)):
            if ordered[j].entry_date > t.exit_date:
                after.add(j)
            if ordered[j].pnl > 0:
                break
    picked = [ordered[j] for j in sorted(after)]
    return {"alerts": alerts, "after_trades": len(picked),
            "after_win_rate": sum(1 for t in picked if t.pnl > 0) / len(picked) if picked else None,
            "after_avg_r": _mean([t.r_multiple for t in picked])}

def summarize(sim, benchmark: List[float], money: MoneyRules, unseen_from: date) -> dict:
    pts = sim.equity
    k = sum(1 for p in pts if p.day < unseen_from)
    earlier_pts, unseen_pts = pts[:k], pts[k:]
    earlier_trades = [t for t in sim.trades if t.entry_date < unseen_from]
    unseen_trades = [t for t in sim.trades if t.entry_date >= unseen_from]
    unseen_start = earlier_pts[-1].equity if earlier_pts else money.capital
    bench_unseen_start = benchmark[k - 1] if k > 0 else money.capital
    whole = period_summary(pts, sim.trades, money.capital)
    earlier = period_summary(earlier_pts, earlier_trades, money.capital)
    unseen = period_summary(unseen_pts, unseen_trades, unseen_start)
    bench = {"whole": _bench(benchmark, money.capital), "unseen": _bench(benchmark[k:], bench_unseen_start)}
    return {
        "unseen_from": unseen_from.isoformat(),
        "whole": whole,
        "earlier": earlier,
        "unseen": unseen,
        "benchmark": bench,
        "years": yearly(pts, sim.trades, money.capital),
        "checklist": evidence_checklist(earlier, unseen, whole, bench["unseen"]),
        "loss_streaks": {"whole": loss_streak_alerts(sim.trades), "unseen": loss_streak_alerts(unseen_trades)},
        "skips": dict(sim.skips),
        "notes": dict(sim.notes),
        "drawdown_pauses": list(getattr(sim, "pauses", [])),
        "warnings": list(WARNINGS),
    }


def clean_json(obj):
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, date):
        return obj.isoformat()
    return obj
