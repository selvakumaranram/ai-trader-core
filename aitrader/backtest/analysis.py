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


def _regime(t: dict) -> str:
    on = (t.get("facts") or {}).get("regime_on")
    return "unknown" if on is None else ("above" if on else "below")


def regime_table(trades: Sequence[dict]) -> List[dict]:
    out = []
    for period in PERIODS:
        for regime in ("above", "below", "unknown"):
            group = [t for t in trades if t["period"] == period and _regime(t) == regime]
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
    ("Worst month", "worst_month", _pct), ("Trades", "trades", lambda v: "—" if v is None else str(v)),
    ("Win rate", "win_rate", _pct), ("Net expectancy (R)", "expectancy_r", _num),
    ("Profit factor", "profit_factor", _num), ("Charges", "total_charges", _rupees),
    ("Charges % of gross profit", "charges_pct_of_gross", _pct),
)


def checklist_result(summary: dict) -> str:
    items = summary.get("checklist") or []
    return f"{sum(1 for c in items if c['passed'])}/{len(items)}" if items else "—"


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


def best_by_unseen_expectancy(rows: Sequence[Tuple[str, dict]]) -> Optional[Tuple[str, dict]]:
    """The run whose unseen-period net expectancy (R) is highest; runs without unseen trades rank last."""
    def key(row):
        e = (row[1]["summary"].get("unseen") or {}).get("expectancy_r")
        return float("-inf") if e is None else e
    return max(rows, key=key) if rows else None
