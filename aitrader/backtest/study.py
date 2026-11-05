"""The A/B fix study: scan history, run each strategy set, write every report and the comparison tables."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from aitrader.backtest.analysis import BEST_RULE, best_run, comparison_markdown
from aitrader.backtest.report import equity_csv, file_stem, report_markdown, trades_csv
from aitrader.backtest.runner import BacktestRequest, create_run, execute_run
from aitrader.backtest.types import NEW_RUN_MONEY, MoneyRules
from aitrader.db import repo
from aitrader.jobs import JobDeps, run_job

OLD_MONEY = MoneyRules()  # ₹20,000, fixed ₹400 risk, 3 positions, ₹7,000 cap, ₹1,000 daily stop, no new controls
SMALL_MONEY = replace(NEW_RUN_MONEY, capital=20_000.0, max_position_value=7_000.0)
STUDY_START, STUDY_END = date(2017, 8, 14), date(2026, 10, 30)

# A money spec is MoneyRules, or ("run", id, overrides): that run's stored money settings with some replaced.
MoneySpec = Union[MoneyRules, Tuple[str, int, dict]]


def resolve_money(conn, spec: MoneySpec, remap: Optional[Dict[int, int]] = None) -> MoneyRules:
    """remap points a referenced run id at another run (tests)."""
    if isinstance(spec, MoneyRules):
        return spec
    _, run_id, overrides = spec
    run_id = (remap or {}).get(run_id, run_id)
    row = repo.backtest_run_row(conn, run_id)
    if row is None:
        raise RuntimeError(f"run #{run_id} not found: its money settings are needed for this study")
    return MoneyRules(**{**row["params"]["money"], **overrides})


# (label, strategy, money, run id the baseline must reproduce)
STUDIES: Dict[str, List[Tuple[str, str, MoneySpec, Optional[int]]]] = {
    "a": [("A-base", "momentum", OLD_MONEY, 21), ("A-fix", "momentum", NEW_RUN_MONEY, None),
          ("A2-100k", "momentum_a2", NEW_RUN_MONEY, None), ("A2-20k", "momentum_a2", SMALL_MONEY, None)],
    "b": [("B-base", "pullback", OLD_MONEY, 22), ("B-fix", "pullback", NEW_RUN_MONEY, None),
          ("B2-100k", "pullback_b2", NEW_RUN_MONEY, None), ("B2-20k", "pullback_b2", SMALL_MONEY, None)],
    # C fix (charge check 0.15) plus the A/B runs that the 0.05 check also distorted
    "c": [("C-base", "breakout", ("run", 33, {}), 33),
          ("C-fix", "breakout", ("run", 33, {"max_charge_to_risk": 0.15}), None),
          ("C2-100k", "breakout_c2", NEW_RUN_MONEY, None), ("C2-20k", "breakout_c2", SMALL_MONEY, None),
          ("A-fix", "momentum", NEW_RUN_MONEY, None), ("A2-100k", "momentum_a2", NEW_RUN_MONEY, None),
          ("B-fix", "pullback", NEW_RUN_MONEY, None), ("B2-100k", "pullback_b2", NEW_RUN_MONEY, None)],
    # B3: B2 at the 0.05 check (run #36), B2 at 0.15, then B3 with winners allowed to run
    # C3: C at 0.15 (run #51), C2, C3 and its variants, and a robustness check without C3's best 5 stocks
    "c3": [("C-fix", "breakout", ("run", 51, {}), 51), ("C2-100k", "breakout_c2", NEW_RUN_MONEY, None),
           ("C3-100k", "breakout_c3", NEW_RUN_MONEY, None),
           ("C3-scaled", "breakout_c3", replace(NEW_RUN_MONEY, dd_risk_scaling=True), None),
           ("C3-half", "breakout_c3", replace(NEW_RUN_MONEY, risk_pct=0.005), None),
           ("C3-20k", "breakout_c3", SMALL_MONEY, None),
           ("C3-ex-top5", "breakout_c3", NEW_RUN_MONEY, None, {"exclude_top5_from": "C3-100k"})],
    "b3": [("B2-base", "pullback_b2", ("run", 36, {}), 36),
           ("B2-c15", "pullback_b2", ("run", 36, {"max_charge_to_risk": 0.15}), None),
           ("B3-100k", "pullback_b3", NEW_RUN_MONEY, None), ("B3-20k", "pullback_b3", SMALL_MONEY, None)],
}


TITLES = {"c": "Combined comparison — C runs and the A/B runs re-run with the 0.15 charge check",
          "b3": "Combined comparison — B2 at both charge checks and B3",
          "c3": "Combined comparison — C at 0.15, C2 and C3 (with the exclude-top-5 robustness check)"}


# A3: single changes vs A2-base — (label, family, strategy overrides, money overrides)
A3_SINGLES = [
    ("A3-notime", "time", {"time_stop_days": None}, {}),
    ("A3-time120", "time", {"time_stop_days": 120}, {}),
    ("A3-rank50", "rank", {"keep_top": 50}, {}),
    ("A3-norank", "rank", {"keep_top": None}, {}),
    ("A3-group", "group", {}, {"max_per_group": 1, "max_per_sector": 2}),
    ("A3-8pos", "pos", {}, {"max_positions": 8, "risk_pct": 0.0075, "max_position_value": 20_000.0}),
]
A3_MAX_DD_INCREASE = 0.02


def select_a3_best(base_summary: dict, singles) -> Tuple[List[str], List[str]]:
    """Pick the single changes for A3-best using ONLY earlier-period numbers: a change qualifies when its earlier
    net R per trade beats A2-base and its earlier max drawdown is at most 2 points higher; within a family (time,
    rank) only the qualifier with the better earlier net R is kept. Returns (chosen labels, reasoning lines)."""
    b = base_summary["earlier"]
    notes = [f"A2-base, earlier years: net {b['expectancy_r']:+.3f}R per trade, max drawdown "
             f"{b['max_drawdown_pct'] * 100:.1f}%. Rule fixed in advance: a change qualifies if its earlier net R is "
             f"higher and its earlier max drawdown is no more than {A3_MAX_DD_INCREASE * 100:.0f} points higher; one "
             "time-stop and one rank variant at most (the better earlier net R)."]
    best: dict = {}
    order: List[str] = []
    for label, family, summary in singles:
        e = summary["earlier"]
        better = e["expectancy_r"] > b["expectancy_r"]
        dd_change = e["max_drawdown_pct"] - b["max_drawdown_pct"]
        ok = better and dd_change <= A3_MAX_DD_INCREASE + 1e-9
        notes.append(f"{label}: earlier net {e['expectancy_r']:+.3f}R ({'higher' if better else 'not higher'}), max "
                     f"drawdown {e['max_drawdown_pct'] * 100:.1f}% ({dd_change * 100:+.1f} points) → "
                     f"{'qualifies' if ok else 'does not qualify'}.")
        if family not in order:
            order.append(family)
        if ok:
            if family in best and best[family][1] == e["expectancy_r"]:
                notes.append(f"{label} qualifies but has the same earlier net R as {best[family][0]}: the first "
                             f"listed ({best[family][0]}) is kept.")
                continue
            if family in best and best[family][1] > e["expectancy_r"]:
                notes.append(f"{label} qualifies but {best[family][0]} has the better earlier net R: not used.")
                continue
            if family in best:
                notes.append(f"{best[family][0]} qualifies but {label} has the better earlier net R: not used.")
            best[family] = (label, e["expectancy_r"])
    chosen = [best[f][0] for f in order if f in best]
    notes.append(f"A3-best = A2-base + {', '.join(chosen)}." if chosen else
                 "No single change qualified: A3-best = A2-base settings.")
    return chosen, notes


def _run_one(engine, label: str, strategy: str, money: MoneyRules, start, end, out_dir: Path, log,
             **request) -> dict:
    log(f"Running {label} ({strategy}) …")
    run_id = create_run(engine, BacktestRequest(strategy, start, end, money, **request))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        run = repo.get_backtest_run(conn, run_id)
    if run["status"] != "ok":
        raise RuntimeError(f"{label} (run #{run_id}) failed: {run['message']}")
    _write(out_dir, label, run)
    log(f"  run #{run_id} done: {(run['summary']['whole'] or {}).get('trades')} trades")
    return run


def run_a3_study(deps: JobDeps, out_dir: Path, start: Optional[date] = None, end: Optional[date] = None,
                 log: Callable[[str], None] = print, base_run: int = 63) -> Dict[str, List[Tuple[str, dict]]]:
    from aitrader.backtest.costs import CostModel
    from aitrader.backtest.groups import load_groups

    out_dir.mkdir(parents=True, exist_ok=True)
    engine = deps.engine
    log("Scanning full history for price jumps first …")
    scan = run_job("scan_history", deps, deps.now().date())
    log(f"  scan_history: {scan.status} {scan.counts}")
    with engine.connect() as conn:
        base_money = resolve_money(conn, ("run", base_run, {}))
        ref = repo.backtest_run_row(conn, base_run)
    rows: List[Tuple[str, dict]] = []
    base = _run_one(engine, "A2-base", "momentum_a2", base_money, start, end, out_dir, log)
    rows.append(("A2-base", base))
    singles = []
    specs = {}
    for label, family, strategy_params, money_changes in A3_SINGLES:
        run = _run_one(engine, label, "momentum_a2", replace(base_money, **money_changes), start, end, out_dir, log,
                       strategy_params=strategy_params)
        rows.append((label, run))
        singles.append((label, family, run["summary"]))
        specs[label] = (strategy_params, money_changes)
    chosen, notes = select_a3_best(base["summary"], singles)
    for line in notes:
        log("  " + line)
    best_params: dict = {}
    best_money: dict = {}
    for label in chosen:
        best_params.update(specs[label][0])
        best_money.update(specs[label][1])
    best_money_rules = replace(base_money, **best_money)
    best = _run_one(engine, "A3-best", "momentum_a2", best_money_rules, start, end, out_dir, log,
                    strategy_params=best_params, notes=tuple(notes))
    rows.append(("A3-best", best))

    adani = tuple(sorted(s for s, g in load_groups().items() if g == "Adani"))
    robustness = [
        ("A3-best ex-Adani", best_money_rules, {"exclude": adani}),
        ("A3-best ex-top5", best_money_rules, {"exclude": tuple(top_earlier_symbols(best))}),
        ("A3-best Wednesday", best_money_rules, {"strategy_params": {**best_params, "signal_weekday": 2}}),
        ("A3-best 2x slippage", best_money_rules,
         {"costs": CostModel(slippage=CostModel().slippage * 2)}),
        ("A3-best 20k", replace(best_money_rules, capital=20_000.0, max_position_value=7_000.0), {}),
    ]
    for label, money, extra in robustness:
        request = {"strategy_params": best_params, **extra}
        rows.append((label, _run_one(engine, label, "momentum_a2", money, start, end, out_dir, log, **request)))

    sections = [comparison_markdown(rows, "Combined comparison — A2-base, single changes, A3-best and robustness "
                                          "checks"),
                "# How A3-best was chosen (earlier-period numbers only)\n\n" + "\n".join(f"- {n}" for n in notes)
                + "\n",
                "# Reproduction check\n\n- " + reproduction_line("A2-base", base["summary"], base_run,
                                                                  ref and ref["summary"]) + "\n"]
    combined = "\n".join(sections)
    (out_dir / "comparison.md").write_text(combined, encoding="utf-8")
    log("")
    for line in combined.splitlines():
        log(line)
    return {"a3": rows}


# C3 improvements (ideas from the trader-* skills): (label, family, strategy overrides, money overrides)
C3X_SINGLES = [
    ("C3-confirm", "entry", {"entry_confirm": True}, {}),  # trader-entry: buy only on confirmation
    ("C3-aplus", "filter", {"volume_surge": 3.0}, {}),  # trader-risk: only the strongest setups
    ("C3-failedbo", "exit", {"failed_breakout_days": 5}, {}),  # selling pressure: back below the breakout level
    ("C3-distrib", "exit", {"distribution_days": 2}, {}),  # selling pressure: 2 heavy-volume down days in 10
]


def _bootstrap_line(label: str, run: dict) -> str:
    from aitrader.backtest.analysis import bootstrap_years

    trades = [t for t in run["trades"] if t["period"] == "earlier"]
    years = max(1.0, (date.fromisoformat(run["summary"]["unseen_from"]) - run["start_date"]).days / 365.25)
    per_year = max(1, round(len(trades) / years))
    b = bootstrap_years([float(t["r_multiple"]) for t in trades], per_year)
    if not b["runs"]:
        return f"- {label}: no earlier trades to resample."
    return (f"- {label} (earlier years resampled {b['runs']:,} times, {per_year} trades a year, seed {b['seed']}): a "
            f"year's total {b['p5_r']:+.1f}R (bad, 5%) / {b['median_r']:+.1f}R (typical) / {b['p95_r']:+.1f}R "
            f"(good, 95%); a losing year {b['losing_year_odds'] * 100:.0f}% of the time; worst losing streak in a "
            f"year typically {b['median_worst_streak']}, 1 year in 20 {b['p95_worst_streak']} or more.")


def run_c3x_study(deps: JobDeps, out_dir: Path, start: Optional[date] = None, end: Optional[date] = None,
                  log: Callable[[str], None] = print, base_run: int = 55) -> Dict[str, List[Tuple[str, dict]]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = deps.engine
    log("Scanning full history for price jumps first …")
    scan = run_job("scan_history", deps, deps.now().date())
    log(f"  scan_history: {scan.status} {scan.counts}")
    with engine.connect() as conn:
        base_money = resolve_money(conn, ("run", base_run, {}))
        ref = repo.backtest_run_row(conn, base_run)
    rows: List[Tuple[str, dict]] = []
    base = _run_one(engine, "C3-base", "breakout_c3", base_money, start, end, out_dir, log)
    rows.append(("C3-base", base))
    singles, specs = [], {}
    for label, family, strategy_params, money_changes in C3X_SINGLES:
        run = _run_one(engine, label, "breakout_c3", replace(base_money, **money_changes), start, end, out_dir,
                       log, strategy_params=strategy_params)
        rows.append((label, run))
        singles.append((label, family, run["summary"]))
        specs[label] = (strategy_params, money_changes)
    chosen, notes = select_a3_best(base["summary"], singles)
    notes = [n.replace("A2-base", "C3-base").replace("A3-best", "C3-best")
             .replace("one time-stop and one rank variant at most", "at most one variant per family (entry, filter, "
                      "exit)") for n in notes]
    params: dict = {}
    money_changes: dict = {}
    for label in chosen:
        params.update(specs[label][0])
        money_changes.update(specs[label][1])
    best = _run_one(engine, "C3-best", "breakout_c3", replace(base_money, **money_changes), start, end, out_dir,
                    log, strategy_params=params, notes=tuple(notes))
    rows.append(("C3-best", best))
    sections = [comparison_markdown(rows, "C3 improvements — base, single changes and C3-best"),
                "# How C3-best was chosen (earlier-period numbers only)\n\n" + "\n".join(f"- {n}" for n in notes)
                + "\n",
                "# Time-machine view: what a normal year can look like\n\n"
                + "\n".join(_bootstrap_line(label, run) for label, run in rows) + "\n\n*Built only from the "
                "earlier years' trades, resampled at random. It shows the range of luck, not a forecast.*\n",
                "# Reproduction check\n\n- " + reproduction_line("C3-base", base["summary"], base_run,
                                                                  ref and ref["summary"]) + "\n"]
    combined = "\n".join(sections)
    (out_dir / "comparison.md").write_text(combined, encoding="utf-8")
    log("")
    for line in combined.splitlines():
        log(line)
    return {"c3x": rows}


def top_earlier_symbols(run: dict, n: int = 5) -> List[str]:
    """The n stocks with the largest ₹ P&L in the run's EARLIER period (never the unseen one)."""
    by: Dict[str, float] = {}
    for t in run["trades"]:
        if t["period"] == "earlier":
            by[t["symbol"]] = by.get(t["symbol"], 0.0) + float(t["pnl"])
    return [s for s, _ in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def reproduction_line(label: str, summary: dict, ref_id: int, ref_summary: Optional[dict]) -> str:
    if ref_summary is None:
        return f"❓ {label}: run #{ref_id} not found — cannot check reproduction"
    got, want = summary.get("whole") or {}, ref_summary.get("whole") or {}
    same = (got.get("trades") == want.get("trades")
            and abs((got.get("return_pct") or 0) - (want.get("return_pct") or 0)) < 0.0005)
    return (f"{'✅' if same else '❌'} {label} vs run #{ref_id}: {got.get('trades')} trades, "
            f"{(got.get('return_pct') or 0) * 100:.1f}% (run #{ref_id}: {want.get('trades')} trades, "
            f"{(want.get('return_pct') or 0) * 100:.1f}%)")


def _write(out_dir: Path, label: str, run: dict) -> None:
    stem = f"{label}-{file_stem(run)}"
    (out_dir / f"{stem}.md").write_text(report_markdown(run), encoding="utf-8")
    (out_dir / f"{stem}-trades.csv").write_text(trades_csv(run), encoding="utf-8-sig")
    (out_dir / f"{stem}-equity.csv").write_text(equity_csv(run), encoding="utf-8-sig")


def run_study(deps: JobDeps, which: Sequence[str], out_dir: Path, start: Optional[date] = None,
              end: Optional[date] = None, log: Callable[[str], None] = print) -> Dict[str, List[Tuple[str, dict]]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = deps.engine
    log("Scanning full history for price jumps first …")
    scan = run_job("scan_history", deps, deps.now().date())
    log(f"  scan_history: {scan.status} {scan.counts}")
    results: Dict[str, List[Tuple[str, dict]]] = {}
    checks: List[str] = []
    for key in which:
        results[key] = []
        for label, strategy, money, ref_id, *extra in STUDIES[key]:
            options = extra[0] if extra else {}
            exclude: Tuple[str, ...] = ()
            if options.get("exclude_top5_from"):
                source = dict(results[key])[options["exclude_top5_from"]]
                exclude = tuple(top_earlier_symbols(source))
            log(f"Running {label} ({strategy}){' without ' + ', '.join(exclude) if exclude else ''} …")
            with engine.connect() as conn:
                money = resolve_money(conn, money)
            run_id = create_run(engine, BacktestRequest(strategy, start, end, money, exclude=exclude))
            execute_run(engine, run_id)
            with engine.connect() as conn:
                run = repo.get_backtest_run(conn, run_id)
                ref = repo.backtest_run_row(conn, ref_id) if ref_id else None
            if run["status"] != "ok":
                raise RuntimeError(f"{label} (run #{run_id}) failed: {run['message']}")
            _write(out_dir, label, run)
            results[key].append((label, run))
            log(f"  run #{run_id} done: {(run['summary']['whole'] or {}).get('trades')} trades")
            if ref_id:
                checks.append(reproduction_line(label, run["summary"], ref_id, ref and ref["summary"]))
    write_comparisons(out_dir, results, checks, log)
    return results


def write_comparisons(out_dir: Path, results: Dict[str, List[Tuple[str, dict]]], checks: Sequence[str],
                      log: Callable[[str], None] = print) -> None:
    sections = []
    for key, rows in results.items():
        md = comparison_markdown(rows, TITLES.get(key, f"Strategy {key.upper()} — comparison"))
        (out_dir / f"comparison-{key.upper()}.md").write_text(md, encoding="utf-8")
        sections.append(md)
    if "a" in results and "b" in results:
        best = [best_run(results["a"]), best_run(results["b"])]
        sections.append(comparison_markdown(best, f"Best A run vs best B run ({BEST_RULE})"))
    if checks:
        sections.append("# Reproduction check\n\n" + "\n".join(f"- {c}" for c in checks) + "\n")
    combined = "\n".join(sections)
    (out_dir / "comparison.md").write_text(combined, encoding="utf-8")
    log("")
    for line in combined.splitlines():
        log(line)
