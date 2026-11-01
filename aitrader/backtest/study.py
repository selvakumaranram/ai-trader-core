"""The A/B fix study: scan history, run each strategy set, write every report and the comparison tables."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from aitrader.backtest.analysis import BEST_RULE, best_run, comparison_markdown
from aitrader.backtest.report import equity_csv, file_stem, report_markdown, trades_csv
from aitrader.backtest.runner import BacktestRequest, create_run, execute_run
from aitrader.backtest.types import NEW_RUN_MONEY, MoneyRules
from aitrader.db import repo
from aitrader.jobs import JobDeps, run_job

OLD_MONEY = MoneyRules()  # ₹20,000, fixed ₹400 risk, 3 positions, ₹7,000 cap, ₹1,000 daily stop, no new controls
SMALL_MONEY = replace(NEW_RUN_MONEY, capital=20_000.0, max_position_value=7_000.0)
STUDY_START, STUDY_END = date(2017, 8, 14), date(2026, 10, 30)

# (label, strategy, money, run id the baseline must reproduce)
STUDIES: Dict[str, List[Tuple[str, str, MoneyRules, Optional[int]]]] = {
    "a": [("A-base", "momentum", OLD_MONEY, 21), ("A-fix", "momentum", NEW_RUN_MONEY, None),
          ("A2-100k", "momentum_a2", NEW_RUN_MONEY, None), ("A2-20k", "momentum_a2", SMALL_MONEY, None)],
    "b": [("B-base", "pullback", OLD_MONEY, 22), ("B-fix", "pullback", NEW_RUN_MONEY, None),
          ("B2-100k", "pullback_b2", NEW_RUN_MONEY, None), ("B2-20k", "pullback_b2", SMALL_MONEY, None)],
}


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
        for label, strategy, money, ref_id in STUDIES[key]:
            log(f"Running {label} ({strategy}) …")
            run_id = create_run(engine, BacktestRequest(strategy, start, end, money))
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
        md = comparison_markdown(rows, f"Strategy {key.upper()} — comparison")
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
