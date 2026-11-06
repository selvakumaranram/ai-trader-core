"""Malaysian SnR v2 research (brief: malaysian-2.md): NIFTY 50 only, controlled one-change-at-a-time experiments,
chronological train / validation / holdout, pre-registered selection, honest verdict.

Selection uses ONLY the training period (2017–2021) to qualify a change and the validation period (2022–2023) to
confirm it. The 2024–2026 holdout is reported, never used to choose. The original baseline rules are unchanged."""
from __future__ import annotations

import csv
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from aitrader.backtest.metrics import cagr, max_drawdown
from aitrader.backtest.types import MoneyRules

DATA = Path(__file__).resolve().parents[1] / "data"

# The brief's research risk settings (section 7). The 10% drawdown is a review threshold (reported), not a pause.
RESEARCH_MONEY = MoneyRules(capital=100_000.0, risk_per_trade=500.0, daily_loss_limit=1_500.0, max_positions=3,
                            max_position_value=25_000.0, risk_mode="percent", risk_pct=0.005, daily_loss_pct=0.015,
                            regime_filter=True, max_charge_to_risk=0.15, max_drawdown_pause=None,
                            reward_multiple=2.0, time_stop_days=20)
DD_REVIEW = 0.10
PERIODS = (("train", date(2017, 8, 14), date(2021, 12, 31)),
           ("validation", date(2022, 1, 1), date(2023, 12, 31)),
           ("holdout", date(2024, 1, 1), date(2026, 10, 30)))
START, END = PERIODS[0][1], PERIODS[-1][2]

# Pre-registered selection rule (fixed before any experiment ran)
MIN_TRAIN_TRADES = 50
MIN_VALIDATION_TRADES = 15
MIN_TRAIN_GAIN_R = 0.03  # training expectancy must beat the baseline by at least this
MAX_TRAIN_DD_INCREASE = 0.05

# (experiment, label, neighbour group or None, strategy overrides, money overrides, what changed)
EXPERIMENTS = [
    (1, "E1-red40", "red", {"red_body_min": 0.4}, {}, "red candle body ≥ 40% of its range"),
    (1, "E1-red60", "red", {"red_body_min": 0.6}, {}, "red candle body ≥ 60% of its range"),
    (1, "E1-green40", "green", {"green_body_min": 0.4}, {}, "green candle body ≥ 40% of its range"),
    (1, "E1-green60", "green", {"green_body_min": 0.6}, {}, "green candle body ≥ 60% of its range"),
    (1, "E1-vol10", "vol", {"green_relvol_min": 1.0}, {}, "green day volume ≥ 1.0× its 20-day average"),
    (1, "E1-vol15", "vol", {"green_relvol_min": 1.5}, {}, "green day volume ≥ 1.5× its 20-day average"),
    (1, "E1-age3", "age", {"min_age": 3}, {}, "touch at least 3 days after the level forms"),
    (1, "E1-age10", "age", {"min_age": 10}, {}, "touch at least 10 days after the level forms"),
    (1, "E1-moved1", "moved", {"moved_atr_min": 1.0}, {}, "price closed ≥ 1 ATR above the level before the touch"),
    (1, "E1-moved2", "moved", {"moved_atr_min": 2.0}, {}, "price closed ≥ 2 ATR above the level before the touch"),
    (2, "E2-ema20", None, {"trend": "ema20"}, {}, "trend = close above 20 EMA (replaces the original)"),
    (2, "E2-ema50", None, {"trend": "ema50"}, {}, "trend = close above 50 EMA"),
    (2, "E2-ema100", None, {"trend": "ema100"}, {}, "trend = close above 100 EMA"),
    (2, "E2-ema200", None, {"trend": "ema200"}, {}, "trend = close above 200 EMA"),
    (2, "E2-ema50>200", None, {"trend": "ema50_200"}, {}, "trend = 50 EMA above 200 EMA"),
    (2, "E2-px>ema50&200", None, {"trend": "above_ema50_200"}, {}, "trend = close above 50 and 200 EMA"),
    (2, "E2-adx20", "adx", {"adx_min": 20}, {}, "original trend + ADX(14) ≥ 20"),
    (2, "E2-adx25", "adx", {"adx_min": 25}, {}, "original trend + ADX(14) ≥ 25"),
    (3, "E3-break", None, {"entry_confirm": True}, {}, "buy only when the next day trades above the touch day's high"),
    (3, "E3-bullish", None, {"bullish_close": True}, {}, "touch day must close green, in its top third"),
    (4, "E4-stop0.75", "stop", {"stop_atr": 0.75}, {}, "stop 0.75 ATR below the level"),
    (4, "E4-stop1.0", "stop", {"stop_atr": 1.0}, {}, "stop 1 ATR below the level"),
    (4, "E4-structure", None, {"stop_mode": "structure"}, {}, "stop 0.25 ATR below the touch day's low"),
    (5, "E5-1.5R", "target", {}, {"reward_multiple": 1.5}, "target 1.5R"),
    (5, "E5-2.5R", "target", {}, {"reward_multiple": 2.5}, "target 2.5R"),
    (5, "E5-3R", "target", {}, {"reward_multiple": 3.0}, "target 3R"),
    (5, "E5-time10", "time", {}, {"time_stop_days": 10}, "time exit 10 days"),
    (5, "E5-time15", "time", {}, {"time_stop_days": 15}, "time exit 15 days"),
    (5, "E5-time30", "time", {}, {"time_stop_days": 30}, "time exit 30 days"),
    (5, "E5-partial1R", None, {"exit_policy": "partial_trail", "partial_r": 1.0, "time_stop_days": 20}, {},
     "sell half at 1R, stop to breakeven, trail the rest under the 5-day low (20-day time exit)"),
    (5, "E5-atrtrail", None, {"exit_policy": "atr_trail", "trail_atr": 3.0, "time_stop_days": 40}, {},
     "no target: 3-ATR trailing stop, 40-day time exit"),
    (6, "E6-no-regime", None, {}, {"regime_filter": False}, "no NIFTY 200-day filter"),
    (6, "E6-nifty50&200", None, {}, {"regime_filter_short": True}, "NIFTY above its 50- AND 200-day averages"),
]
NOT_TESTED = [
    "Entry on a support retest and limit orders near support (C4, C6): need intraday fills the daily engine does not "
    "model honestly.",
    "Trailing stop only after 1.5R: covered in spirit by the partial-at-1R and ATR-trail variants.",
    "NIFTY volatility / ATR-percentile regimes and market breadth (F): no breadth history; results are shown by NIFTY "
    "trend regime instead.",
    "Stock selection by past strategy expectancy (G): rejected by the brief's own rule (in-sample picking).",
]


def load_nifty50(path: Path = DATA / "nifty50.csv") -> List[str]:
    """The current NIFTY 50 members (survivorship bias: past members that left the index are not included)."""
    with path.open(encoding="utf-8") as f:
        return [row["symbol"].strip() for row in csv.DictReader(f) if row["symbol"].strip()]


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def period_metrics(run: dict, start: date, end: date) -> dict:
    trades = [t for t in run["trades"] if start <= t["entry_date"] <= end]
    rs = [float(t["r_multiple"]) for t in trades]
    pnl = [float(t["pnl"]) for t in trades]
    wins = [float(t["r_multiple"]) for t in trades if float(t["pnl"]) > 0]
    losses = [float(t["r_multiple"]) for t in trades if float(t["pnl"]) <= 0]
    gross_win, gross_loss = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
    ratios = [float(t["facts"]["charge_to_risk"]) for t in trades if (t["facts"] or {}).get("charge_to_risk")]
    out = {"trades": len(trades), "win_rate": len(wins) / len(trades) if trades else None,
           "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
           "expectancy_r": _mean(rs), "avg_win_r": _mean(wins), "avg_loss_r": _mean(losses),
           "charges": sum(float(t["charges"]) for t in trades), "cost_to_risk": _mean(ratios),
           "net_pnl": sum(pnl), "return_pct": None, "cagr": None, "max_drawdown_pct": None, "bench_return_pct": None}
    before = [p for p in run["equity"] if p["trade_date"] < start]
    inside = [p for p in run["equity"] if start <= p["trade_date"] <= end]
    if inside:
        first = before[-1] if before else inside[0]
        s0, b0 = float(first["equity"]), float(first["benchmark_equity"])
        values = [float(p["equity"]) for p in inside]
        out.update(return_pct=values[-1] / s0 - 1,
                   cagr=cagr(s0, values[-1], first["trade_date"], inside[-1]["trade_date"]),
                   max_drawdown_pct=max_drawdown([s0] + values)[0],
                   bench_return_pct=float(inside[-1]["benchmark_equity"]) / b0 - 1)
    return out


def select_variants(base: dict, rows: Sequence[dict]) -> Tuple[Dict[int, str], List[str]]:
    """Pre-registered: a change qualifies on TRAINING (≥50 trades, expectancy ≥ baseline + 0.03R, higher profit
    factor, drawdown at most 5 points worse, and — for a tuned number — at least one neighbouring value also beating
    the baseline), then must beat the baseline's expectancy in VALIDATION (≥15 trades). Per experiment, the
    qualifying change with the best training expectancy is chosen. The holdout is never used."""
    bt, bv = base["train"], base["validation"]
    notes = [f"Baseline: training {bt['expectancy_r']:+.3f}R (PF {bt['profit_factor']:.2f}, {bt['trades']} trades), "
             f"validation {bv['expectancy_r']:+.3f}R."]
    passed: Dict[int, Tuple[str, float]] = {}

    def beats(t):
        return t["expectancy_r"] is not None and t["expectancy_r"] > bt["expectancy_r"]

    for row in rows:
        t, v, label = row["train"], row["validation"], row["label"]
        if t["trades"] < MIN_TRAIN_TRADES or t["expectancy_r"] is None:
            notes.append(f"{label}: too few training trades ({t['trades']}) — rejected.")
            continue
        if not (t["expectancy_r"] >= bt["expectancy_r"] + MIN_TRAIN_GAIN_R
                and (t["profit_factor"] or 0) > (bt["profit_factor"] or 0)):
            notes.append(f"{label}: training {t['expectancy_r']:+.3f}R, PF {t['profit_factor'] or 0:.2f} — not "
                         f"clearly better than the baseline.")
            continue
        if (t["max_drawdown_pct"] or 0) > (bt["max_drawdown_pct"] or 0) + MAX_TRAIN_DD_INCREASE:
            notes.append(f"{label}: training drawdown {t['max_drawdown_pct'] * 100:.1f}% is too much higher — "
                         f"rejected.")
            continue
        if row.get("group") and not any(beats(o["train"]) for o in rows
                                         if o.get("group") == row["group"] and o["label"] != label):
            notes.append(f"{label}: better in training, but no neighbouring value also beats the baseline (a lone "
                         f"peak) — rejected.")
            continue
        if v["trades"] < MIN_VALIDATION_TRADES or v["expectancy_r"] is None or v["expectancy_r"] <= bv["expectancy_r"]:
            notes.append(f"{label}: better in training ({t['expectancy_r']:+.3f}R) but fails validation "
                         f"({(v['expectancy_r'] or 0):+.3f}R vs baseline {bv['expectancy_r']:+.3f}R, "
                         f"{v['trades']} trades) — rejected.")
            continue
        notes.append(f"{label}: qualifies (training {t['expectancy_r']:+.3f}R, validation {v['expectancy_r']:+.3f}R).")
        if row["exp"] not in passed or t["expectancy_r"] > passed[row["exp"]][1]:
            passed[row["exp"]] = (label, t["expectancy_r"])
    chosen = {e: lab for e, (lab, _) in sorted(passed.items())}
    notes.append("Chosen per experiment: " + (", ".join(f"Exp {e}: {lab}" for e, lab in chosen.items())
                                               if chosen else "none — no change was validated."))
    return chosen, notes


def feature_buckets(trades: Sequence[dict], key: str, edges: Sequence[float]) -> List[dict]:
    """Win rate and expectancy by fixed buckets of one recorded trade fact (edges fixed in advance)."""
    labels = ([f"< {edges[0]:g}"] + [f"{a:g} – {b:g}" for a, b in zip(edges, edges[1:])] + [f"≥ {edges[-1]:g}"])
    groups: Dict[str, List[float]] = {lab: [] for lab in labels}
    unknown: List[float] = []
    for t in trades:
        v = (t["facts"] or {}).get(key)
        r = float(t["r_multiple"])
        if v is None:
            unknown.append(r)
            continue
        i = sum(1 for e in edges if float(v) >= e)
        groups[labels[i]].append(r)
    out = [{"bucket": lab, "n": len(rs), "win_rate": (sum(1 for r in rs if r > 0) / len(rs)) if rs else None,
            "expectancy_r": _mean(rs)} for lab, rs in groups.items()]
    if unknown:
        out.append({"bucket": "unknown", "n": len(unknown),
                    "win_rate": sum(1 for r in unknown if r > 0) / len(unknown), "expectancy_r": _mean(unknown)})
    return out


# --- running and reporting ---------------------------------------------------------------------------------------

def _f(x, digits=2, pct=False, sign=False):
    if x is None:
        return "—"
    if pct:
        return f"{x * 100:+.1f}%" if sign else f"{x * 100:.1f}%"
    return f"{x:+.{digits}f}" if sign else f"{x:.{digits}f}"


def _period_table(title: str, rows: List[Tuple[str, dict]], period: str) -> str:
    out = [f"### {title}", "", "| Run | Trades | Win rate | Profit factor | Expectancy (R) | Avg win (R) | Avg loss (R) "
           "| Return | CAGR | Max drawdown | NIFTY 50 buy & hold |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, m in rows:
        p = m[period]
        out.append(f"| {label} | {p['trades']} | {_f(p['win_rate'], pct=True)} | {_f(p['profit_factor'])} | "
                   f"{_f(p['expectancy_r'], 3, sign=True)} | {_f(p['avg_win_r'])} | {_f(p['avg_loss_r'])} | "
                   f"{_f(p['return_pct'], pct=True, sign=True)} | {_f(p['cagr'], pct=True, sign=True)} | "
                   f"{_f(p['max_drawdown_pct'], pct=True)} | {_f(p['bench_return_pct'], pct=True, sign=True)} |")
    return "\n".join(out) + "\n"


def _metrics(run: dict) -> dict:
    m = {name: period_metrics(run, s, e) for name, s, e in PERIODS}
    m["whole"] = period_metrics(run, START, END)
    m["skips"] = sum((run["summary"].get("skips") or {}).values())
    m["years"] = run["summary"].get("years") or []
    m["run_id"] = run["id"]
    return m


def _regime_table(run: dict) -> str:
    buckets: Dict[str, List[float]] = {}
    for t in run["trades"]:
        f = t["facts"] or {}
        above200, above50 = f.get("regime_on"), f.get("nifty_above_sma50")
        name = ("Bull (NIFTY above 50- and 200-day)" if above200 and above50 else
                "Pullback in bull (above 200, below 50)" if above200 else
                "Bear (below 200-day)" if above50 is False else "Recovering (below 200, above 50)")
        buckets.setdefault(name, []).append(float(t["r_multiple"]))
    rows = ["| NIFTY regime at the signal | Trades | Win rate | Expectancy (R) |", "|---|---|---|---|"]
    for name, rs in sorted(buckets.items()):
        rows.append(f"| {name} | {len(rs)} | {_f(sum(1 for r in rs if r > 0) / len(rs), pct=True)} | "
                    f"{_f(_mean(rs), 3, sign=True)} |")
    return "\n".join(rows) + "\n"


def _years_table(rows: List[Tuple[str, dict]]) -> str:
    years = sorted({y["year"] for _, m in rows for y in m["years"]})
    head = "| Year | " + " | ".join(lab for lab, _ in rows) + " |"
    out = [head, "|---" * (len(rows) + 1) + "|"]
    for y in years:
        cells = []
        for _, m in rows:
            hit = next((x for x in m["years"] if x["year"] == y), None)
            cells.append(f"{_f(hit['return_pct'], pct=True, sign=True)} ({hit['trades']} tr)" if hit else "—")
        out.append(f"| {y} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def _diagnosis(base_run: dict, m: dict) -> str:
    trades = base_run["trades"]
    train = [t for t in trades if PERIODS[0][1] <= t["entry_date"] <= PERIODS[0][2]]
    gross = sum(float(t["pnl"]) + float(t["charges"]) for t in trades)
    charges = sum(float(t["charges"]) for t in trades)
    exits: Dict[str, int] = {}
    for t in trades:
        exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
    losers = [float((t["facts"] or {}).get("mfe_r") or 0) for t in trades if float(t["pnl"]) <= 0]
    lines = [f"- Whole run: {len(trades)} trades, gross P&L before charges ₹{gross:,.0f}, charges ₹{charges:,.0f}, "
             f"net ₹{gross - charges:,.0f}.",
             "- Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(exits.items(), key=lambda kv: -kv[1])) + ".",
             f"- Losing trades reached on average {_f(_mean(losers))}R in their favour before losing (MFE).",
             "", "**What separates winners from losers (TRAINING trades only, fixed buckets):**", ""]
    specs = [("red_body", [0.33, 0.66], "Red candle body ÷ range"), ("green_body", [0.33, 0.66],
             "Green candle body ÷ range"), ("green_relvol", [0.8, 1.2], "Green day volume ÷ 20-day avg"),
             ("age", [3, 10], "Days from level to touch"), ("moved_atr", [1, 2], "Move above the level before the "
             "touch (ATR)"), ("atr_pct", [0.015, 0.025], "ATR ÷ price at the signal")]
    for key, edges, title in specs:
        lines += [f"*{title}*", "", "| Bucket | Trades | Win rate | Expectancy (R) |", "|---|---|---|---|"]
        for b in feature_buckets(train, key, edges):
            lines.append(f"| {b['bucket']} | {b['n']} | {_f(b['win_rate'], pct=True)} | "
                         f"{_f(b['expectancy_r'], 3, sign=True)} |")
        lines.append("")
    by_sector: Dict[str, List[float]] = {}
    for t in train:
        by_sector.setdefault((t["facts"] or {}).get("sector") or "unknown", []).append(float(t["r_multiple"]))
    lines += ["*By sector (training; descriptive only — not used to pick stocks)*", "",
              "| Sector | Trades | Expectancy (R) |", "|---|---|---|"]
    for sec, rs in sorted(by_sector.items(), key=lambda kv: -len(kv[1]))[:12]:
        lines.append(f"| {sec} | {len(rs)} | {_f(_mean(rs), 3, sign=True)} |")
    return "\n".join(lines) + "\n"


def verdict(final: dict) -> Tuple[str, str]:
    v, h = final["validation"], final["holdout"]
    if (v["expectancy_r"] or 0) <= 0 or (h["expectancy_r"] or 0) <= 0:
        return "REJECTED", ("The best validated version does not make money after costs in validation and/or the "
                            "holdout.")
    targets = ((v["profit_factor"] or 0) > 1.3 and (v["expectancy_r"] or 0) > 0.15
               and (h["profit_factor"] or 0) > 1.3 and (h["expectancy_r"] or 0) > 0.15
               and max(v["max_drawdown_pct"] or 1, h["max_drawdown_pct"] or 1) < 0.15)
    if targets:
        return "PROMISING — paper-trade before any real use", "Meets the brief's research targets in validation and holdout."
    return "INCONCLUSIVE", "Positive after costs but below the brief's targets (PF > 1.3, > +0.15R, drawdown < 15%)."


def run_msnr_study(engine, out_dir: Path, log: Callable[[str], None] = print,
                   reproduce_run: int = 90) -> dict:
    from aitrader.backtest.study import _run_one, reproduction_line, resolve_money
    from aitrader.db import repo

    out_dir.mkdir(parents=True, exist_ok=True)
    nifty50 = set(load_nifty50())
    with engine.connect() as conn:
        universe = {i["symbol"] for i in repo.active_instruments(conn) if i["kind"] == "stock"}
        old_money = resolve_money(conn, ("run", reproduce_run, {}))
        ref = repo.backtest_run_row(conn, reproduce_run)
    missing = sorted(nifty50 - universe)
    exclude = tuple(sorted(universe - nifty50))

    def run(label, strategy="msnr", money=RESEARCH_MONEY, params=None, notes=(), excl=exclude):
        return _run_one(engine, label, strategy, money, START, END, out_dir, log, strategy_params=params or {},
                        exclude=excl, notes=tuple(notes))

    repro = _run_one(engine, "Exp0a-reproduce", "msnr", old_money, START, END, out_dir, log)
    base_run = run("Exp0-baseline", notes=("MSNR v2 study baseline: original rules, NIFTY 50, research money",))
    base = _metrics(base_run)
    rows, results = [], [("Exp0 baseline", base)]
    specs = {}
    for exp, label, group, params, money_changes, what in EXPERIMENTS:
        r = run(label, money=replace(RESEARCH_MONEY, **money_changes), params=params, notes=(what,))
        m = _metrics(r)
        rows.append({"label": label, "exp": exp, "group": group, "train": m["train"],
                     "validation": m["validation"]})
        results.append((label, m))
        specs[label] = (params, money_changes, what)
    chosen, notes = select_variants(base, rows)
    params, money_changes, whats = {}, {}, []
    for label in chosen.values():
        params.update(specs[label][0])
        money_changes.update(specs[label][1])
        whats.append(f"{label}: {specs[label][2]}")
    final_run = run("Exp7-v2", money=replace(RESEARCH_MONEY, **money_changes), params=params,
                    notes=tuple(whats) or ("no change validated: same as the baseline",))
    final = _metrics(final_run)
    results.append(("Exp7 v2 (combined)", final))
    a2 = _metrics(run("Bench-A2", strategy="momentum_a2"))
    c3 = _metrics(run("Bench-C3", strategy="breakout_c3"))
    word, why = verdict(final)

    sections = [
        "# Malaysian SnR v2 — research report (NIFTY 50)\n",
        f"**Verdict: {word}.** {why}\n",
        "*Past behaviour on historical data only — not a promise of future profit and not investment advice.*\n",
        "## Set-up\n",
        f"- Universe: the {len(nifty50)} current NIFTY 50 stocks ({len(nifty50) - len(missing)} with data). "
        "Survivorship bias: stocks that left the index are not included, which flatters every result.",
        f"- Dates: {START} → {END}. Training {PERIODS[0][1]}–{PERIODS[0][2]}, validation "
        f"{PERIODS[1][1]}–{PERIODS[1][2]}, holdout {PERIODS[2][1]}–{PERIODS[2][2]} (already seen in the earlier "
        "report, so not truly untouched; never used to choose).",
        "- Money: ₹1,00,000, 0.5% risk per trade, 3 positions, ₹25,000 per stock, 1.5% daily loss stop, Zerodha "
        "delivery charges + slippage, skip trades whose charges exceed 15% of the risk. Daily bars; a bar touching "
        "both stop and target counts the stop first.",
        "- Each experiment changes ONE thing against the unchanged baseline. Selection rule fixed in advance (see "
        "below).\n",
        "## Reproduction of the original run\n",
        "- " + reproduction_line("Original rules on the 200-stock universe", repro["summary"], reproduce_run,
                                 ref and ref["summary"]) + "\n",
        "## Why the original loses (diagnosis of the NIFTY 50 baseline)\n",
        _diagnosis(base_run, base),
        "## Experiment results\n",
        _period_table("Training 2017–2021 (used to qualify)", results, "train"),
        _period_table("Validation 2022–2023 (used to confirm)", results, "validation"),
        _period_table("Holdout 2024–2026 (reported only)", results, "holdout"),
        "### Whole run: costs and skipped signals\n",
        "| Run | Trades | Charges | Charges ÷ risk (avg) | Net P&L | Skipped signals | Max drawdown | Over the 10% "
        "review line? |", "|---|---|---|---|---|---|---|---|",
        *[f"| {lab} | {m['whole']['trades']} | ₹{m['whole']['charges']:,.0f} | {_f(m['whole']['cost_to_risk'], pct=True)} "
          f"| ₹{m['whole']['net_pnl']:,.0f} | {m['skips']} | {_f(m['whole']['max_drawdown_pct'], pct=True)} | "
          f"{'yes' if (m['whole']['max_drawdown_pct'] or 0) > DD_REVIEW else 'no'} |" for lab, m in results],
        "", "## How v2 was chosen (training + validation only)\n", *[f"- {n}" for n in notes], "",
        "**v2 rules:** " + ("original rules plus " + "; ".join(whats) if whats else
                            "no change survived — v2 is the original.") + "\n",
        "## v2 vs the baseline, year by year\n", _years_table([("Baseline", base), ("v2", final)]),
        "## By NIFTY market regime (whole run)\n", "**Baseline**\n", _regime_table(base_run), "**v2**\n",
        _regime_table(final_run),
        "## Against your other strategies and NIFTY 50 (same universe, dates, money and costs)\n",
        "Your ~177% strategy is taken to be A2 (momentum: +176% on the 200-stock universe at 1% risk). Here it runs "
        "under the same NIFTY 50 universe and 0.5% risk / 3-position rules, so its numbers differ.\n",
        _period_table("Training", [("MSNR v2", final), ("A2 momentum", a2), ("C3 breakout", c3)], "train"),
        _period_table("Validation", [("MSNR v2", final), ("A2 momentum", a2), ("C3 breakout", c3)], "validation"),
        _period_table("Holdout", [("MSNR v2", final), ("A2 momentum", a2), ("C3 breakout", c3)], "holdout"),
        "## Not tested, and why\n", *[f"- {n}" for n in NOT_TESTED], "",
        "## Next step\n",
        "- If the verdict is not REJECTED: paper-trade v2 in Shadow Trading for months before any real money.",
        "- If REJECTED: stop spending time on MSNR; keep C3 (and A2) as the tested strategies.",
    ]
    text = "\n".join(sections)
    (out_dir / "msnr-v2-report.md").write_text(text, encoding="utf-8")
    log(f"Report: {out_dir / 'msnr-v2-report.md'} — verdict {word}")
    return {"verdict": word, "chosen": chosen, "final_run": final["run_id"], "baseline_run": base["run_id"]}
