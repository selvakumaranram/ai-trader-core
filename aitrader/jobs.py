from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Dict, List, Optional

from sqlalchemy.engine import Engine

from aitrader.auth import IST
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.kite.gateway import KiteAuthError, KiteGateway
from aitrader.models import Issue, OptionContract
from aitrader.options import select_contracts
from aitrader.quality import (
    BAR_CHECKS,
    check_jumps,
    check_missing,
    check_ohlc,
    check_source_mismatch,
    check_stale,
    find_unknown_closed_days,
    HISTORY_CHECKS,
    scan_history_jumps,
)
from aitrader.redaction import redact_text
from aitrader.sources.yfinance_source import DailyBarSource

log = logging.getLogger(__name__)

UNDERLYING_QUOTE_KEYS = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK"}
QUOTE_BATCH = 500
QUALITY_LOOKBACK_DAYS = 60
HISTORY_JUMP_THRESHOLD = 0.35  # one-day moves beyond this with no recorded split/bonus are flagged
NOT_LOGGED_IN = "Kite not logged in"
SESSION_EXPIRED = "Kite session expired — log in again"
DAILY_DATA_FINAL_AT = time(16, 30)  # a session daily bar is only final after the close
SNAPSHOT_DUE_AT = time(15, 45)
TRAILING_DAYS = 5  # daily ingest re-fetches this many sessions to repair misses and late corrections


@dataclass
class JobDeps:
    engine: Engine
    kite: Callable[[], Optional[KiteGateway]]
    bar_source: DailyBarSource
    calendar: TradingCalendar
    now: Callable[[], datetime] = field(default=lambda: datetime.now(IST))
    on_auth_error: Callable[[], None] = field(default=lambda: None)


@dataclass
class RunResult:
    status: str
    counts: dict
    message: str = ""


def _record_missed_snapshot(deps: JobDeps, run_id: int, run_date: date, reason: str) -> None:
    with deps.engine.begin() as conn:
        repo.insert_issues(conn, run_id, [Issue("missed_snapshot", "warn", run_date,
                                                f"no option snapshot: {reason} (cannot be fetched later)")])


def sync_instruments(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    kite = deps.kite()
    if kite is None:
        return RunResult("skipped", {}, NOT_LOGGED_IN)
    nse, nfo = kite.instruments("NSE"), kite.instruments("NFO")
    contracts = [
        OptionContract(r["name"], r["expiry"], float(r["strike"]), r["instrument_type"], int(r["lot_size"]),
                       r["tradingsymbol"], int(r["instrument_token"]))
        for r in nfo
        if r.get("name") in UNDERLYING_QUOTE_KEYS and r.get("instrument_type") in ("CE", "PE")
        and r.get("segment") == "NFO-OPT" and isinstance(r.get("expiry"), date)
    ]
    with deps.engine.begin() as conn:
        known = {(i["exchange"], i["symbol"]) for i in repo.active_instruments(conn)}
        tokens = {("NSE", r["tradingsymbol"]): int(r["instrument_token"]) for r in nse
                  if ("NSE", r["tradingsymbol"]) in known}
        updated = repo.set_kite_tokens(conn, tokens)
        stored = repo.upsert_option_contracts(conn, contracts, seen_on=run_date)
    return RunResult("ok", {"instrument_tokens": updated, "option_contracts": stored})


def _best_price(levels) -> Optional[float]:
    if levels and levels[0].get("price"):
        return float(levels[0]["price"])
    return None


def snapshot_options(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    kite = deps.kite()
    if kite is None:
        return RunResult("skipped", {}, NOT_LOGGED_IN)
    with deps.engine.connect() as conn:
        synced_today = repo.last_run_date(conn, "sync_instruments", ("ok",)) == run_date
    if not synced_today:
        sync_instruments(deps, run_date, run_id)  # the 08:30 sync is usually skipped: login happens later
    with deps.engine.connect() as conn:
        live = repo.live_option_contracts(conn, run_date)
    if not live:
        return RunResult("failed", {}, "no option contracts available from Kite")
    ids = {c.tradingsymbol: cid for cid, c in live}
    contracts = [c for _, c in live]
    underlying_quotes = kite.quote(list(UNDERLYING_QUOTE_KEYS.values()))
    snapshot_at = deps.now()
    rows: List[dict] = []
    selected_total = 0
    for underlying, key in UNDERLYING_QUOTE_KEYS.items():
        quote = underlying_quotes.get(key)
        if not quote:
            continue
        price = float(quote["last_price"])
        selected = select_contracts(contracts, underlying, price, run_date)
        selected_total += len(selected)
        for start in range(0, len(selected), QUOTE_BATCH):
            batch = selected[start:start + QUOTE_BATCH]
            quotes = kite.quote([f"NFO:{c.tradingsymbol}" for c in batch])
            for c in batch:
                data = quotes.get(f"NFO:{c.tradingsymbol}")
                if not data:
                    continue
                depth = data.get("depth") or {}
                rows.append(dict(contract_id=ids[c.tradingsymbol], snapshot_at=snapshot_at,
                                 last_price=data["last_price"], bid=_best_price(depth.get("buy")),
                                 ask=_best_price(depth.get("sell")), volume=data.get("volume"),
                                 oi=data.get("oi"), underlying_price=price, source="kite"))
    with deps.engine.begin() as conn:
        stored = repo.insert_option_snapshots(conn, rows)
    counts = {"selected": selected_total, "snapshots": stored}
    if stored == 0:
        return RunResult("failed", counts, "no option quotes returned")
    return RunResult("ok" if stored == selected_total else "partial", counts)


def _ingest(deps: JobDeps, instruments: List[dict], start: date, end: date) -> Dict[str, object]:
    counts = {"instruments": len(instruments), "inserted": 0, "updated": 0, "empty": 0, "failed": 0}
    notes: List[str] = []
    for inst in instruments:
        try:
            bars, actions = deps.bar_source.fetch(inst["yf_symbol"], start, end)
            if not bars:
                counts["empty"] += 1
                notes.append(f"{inst['symbol']}: empty")
                continue
            with deps.engine.begin() as conn:
                inserted, updated = repo.upsert_daily_bars(conn, inst["id"], bars, source=deps.bar_source.name,
                                                           price_basis=deps.bar_source.price_basis)
                repo.upsert_corporate_actions(conn, inst["id"], actions, source=deps.bar_source.name)
        except Exception as exc:  # noqa: BLE001 - one bad symbol (fetch or write) must not stop the run
            counts["failed"] += 1
            notes.append(f"{inst['symbol']}: {type(exc).__name__}")
            continue
        counts["inserted"] += inserted
        counts["updated"] += updated
    return {"counts": counts, "notes": notes}


def _status_for(counts: dict) -> str:
    problems = counts["failed"] + counts["empty"]
    if problems == 0:
        return "ok"
    return "failed" if problems >= counts["instruments"] else "partial"


def _last_final_day(deps: JobDeps) -> date:
    """The most recent trading day whose daily bar is complete (today only after 16:30 IST)."""
    now = deps.now()
    day = now.date()
    if deps.calendar.is_trading_day(day) and now.time() >= DAILY_DATA_FINAL_AT:
        return day
    day -= timedelta(days=1)
    while not deps.calendar.is_trading_day(day):
        day -= timedelta(days=1)
    return day


def ingest_daily_bars(deps: JobDeps, run_date: date, run_id: int, start: Optional[date] = None) -> RunResult:
    end = min(run_date, _last_final_day(deps))
    if start is None:
        window = deps.calendar.trading_days(end - timedelta(days=21), end)
        start = window[-TRAILING_DAYS] if len(window) >= TRAILING_DAYS else end
    with deps.engine.connect() as conn:
        instruments = [i for i in repo.active_instruments(conn) if i["yf_symbol"]]
    result = _ingest(deps, instruments, start, end)
    counts = result["counts"]
    return RunResult(_status_for(counts), counts, "; ".join(result["notes"][:20]))


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def backfill_history(deps: JobDeps, run_date: date, run_id: int, years: int = 10) -> RunResult:
    start = _years_before(run_date, years)
    end = min(run_date, _last_final_day(deps))
    with deps.engine.connect() as conn:
        todo, skipped = [], 0
        for inst in repo.active_instruments(conn):
            if not inst["yf_symbol"]:
                continue
            first, last = repo.bar_date_range(conn, inst["id"], deps.bar_source.name)
            if first and last and first <= start + timedelta(days=7) and last >= end:
                skipped += 1
            else:
                todo.append(inst)
    result = _ingest(deps, todo, min(start, end), end)
    counts = {**result["counts"], "skipped": skipped}
    status = "ok" if not todo else _status_for(result["counts"])
    return RunResult(status, counts, "; ".join(result["notes"][:20]))


def run_quality_checks(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    window_start = run_date - timedelta(days=QUALITY_LOOKBACK_DAYS)
    expected = deps.calendar.trading_days(window_start, run_date)
    source = deps.bar_source.name
    issues: List[Issue] = []
    checked = no_data = 0
    with deps.engine.connect() as conn:
        any_days = repo.days_with_any_bar(conn, window_start, run_date, source)
        first_any = min(any_days) if any_days else None
        closed = set(find_unknown_closed_days(any_days, [d for d in expected if first_any and d >= first_any]))
        issues += [Issue("unknown_closed_day", "info", d,
                         "no bars for any instrument; add to the holiday file if NSE was closed")
                   for d in sorted(closed)]
        for inst in repo.active_instruments(conn):
            bars = repo.load_bars(conn, inst["id"], source=source, start=window_start, end=run_date)
            if not bars:
                no_data += 1
                continue
            checked += 1
            iid = inst["id"]
            expected_here = [d for d in expected if d >= bars[0].trade_date]
            issues += check_missing(bars, expected_here, closed, iid)
            issues += check_ohlc(bars, iid)
            issues += check_jumps(bars, repo.action_dates(conn, iid), instrument_id=iid)
            issues += check_stale(bars, instrument_id=iid)
            if source != "kite" and repo.price_basis_of(conn, iid, "kite") == deps.bar_source.price_basis:
                kite_bars = repo.load_bars(conn, iid, source="kite", start=window_start, end=run_date)
                issues += check_source_mismatch(bars, kite_bars, instrument_id=iid)
        first_snap = repo.first_snapshot_day(conn)
        if first_snap is not None:
            now = deps.now()
            have = repo.snapshot_days(conn, first_snap, run_date)
            already = repo.issue_dates(conn, "missed_snapshot", first_snap, run_date)
            for d in deps.calendar.trading_days(max(first_snap, window_start), run_date):
                due = d < now.date() or (d == now.date() and now.time() >= SNAPSHOT_DUE_AT)
                if due and d not in have and d not in already and d not in closed:
                    issues.append(Issue("missed_snapshot", "warn", d,
                                        "no option snapshot recorded (cannot be fetched later)"))
    with deps.engine.begin() as conn:
        repo.delete_issues(conn, BAR_CHECKS, window_start, run_date)
        repo.insert_issues(conn, run_id, issues)
    return RunResult("ok", {"instruments_checked": checked, "no_data": no_data, "issues": len(issues)})


def scan_history(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    """Flag unexplained one-day price jumps across each instrument's whole history (e.g. an unadjusted bonus)."""
    source = deps.bar_source.name
    flags: List[Issue] = []
    scanned = 0
    with deps.engine.connect() as conn:
        for inst in repo.active_instruments(conn):
            bars = repo.load_bars(conn, inst["id"], source=source)
            if not bars:
                continue
            scanned += 1
            flags += scan_history_jumps(bars, repo.corporate_actions_of(conn, inst["id"]),
                                        HISTORY_JUMP_THRESHOLD, instrument_id=inst["id"])
    with deps.engine.begin() as conn:
        repo.delete_issues(conn, HISTORY_CHECKS, date.min, date.max)
        repo.insert_issues(conn, run_id, flags)
    return RunResult("ok", {"instruments": scanned, "flags": len(flags)})

_JOBS: Dict[str, Callable[..., RunResult]] = {
    "sync_instruments": sync_instruments,
    "snapshot_options": snapshot_options,
    "ingest_daily_bars": ingest_daily_bars,
    "run_quality_checks": run_quality_checks,
    "scan_history": scan_history,
    "backfill_history": backfill_history,
}
JOB_NAMES = tuple(_JOBS)

from aitrader.shadow import live as _shadow  # noqa: E402  (after the core jobs: shadow.live imports from here)

_JOBS.update({"shadow_scan": _shadow.shadow_scan, "shadow_enter": _shadow.shadow_enter,
              "shadow_exit": _shadow.shadow_exit})
JOB_NAMES = tuple(_JOBS)


def run_job(name: str, deps: JobDeps, run_date: date, **kwargs) -> RunResult:
    if name not in _JOBS:
        raise ValueError(f"unknown job {name!r}; expected one of {JOB_NAMES}")
    with deps.engine.begin() as conn:
        run_id = repo.start_run(conn, name, run_date, source=getattr(deps.bar_source, "name", None))
    try:
        result = _JOBS[name](deps, run_date, run_id, **kwargs)
    except KiteAuthError:
        deps.on_auth_error()
        result = RunResult("failed", {}, SESSION_EXPIRED)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the run row
        log.exception("job %s failed", name)
        result = RunResult("failed", {}, redact_text(f"{type(exc).__name__}: {exc}")[:500])
    with deps.engine.begin() as conn:
        repo.finish_run(conn, run_id, result.status, result.counts, result.message)
    if name == "backfill_history" and result.status in ("ok", "partial"):
        run_job("scan_history", deps, run_date)  # new history can contain unadjusted bonuses/splits
    if name == "snapshot_options" and result.status in ("skipped", "failed"):
        _record_missed_snapshot(deps, run_id, run_date, result.message or result.status)
    return result
