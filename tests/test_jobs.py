from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from aitrader.auth import IST
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.db.schema import daily_bars, option_snapshots
from aitrader.jobs import JobDeps, run_job
from aitrader.kite.fake import FakeKiteGateway
from aitrader.models import Bar
from fakes import FakeBarSource
from kite_samples import index_quotes, nfo_noise_rows, nfo_option_rows, nse_rows, option_quotes

RUN_DAY = date(2026, 10, 21)  # Wednesday


def deps(engine, kite=None, bars=None, auth_errors=None):
    return JobDeps(
        engine=engine,
        kite=lambda: kite,
        bar_source=FakeBarSource(bars or {}),
        calendar=TradingCalendar(),
        now=lambda: datetime(2026, 10, 21, 17, 0, tzinfo=IST),
        on_auth_error=(lambda: auth_errors.append(1)) if auth_errors is not None else (lambda: None),
    )


def seed(engine):
    with engine.begin() as conn:
        for kind, symbol, yf in (("stock", "INFY", "INFY.NS"), ("stock", "TCS", "TCS.NS"),
                                 ("index", "NIFTY 50", "^NSEI"), ("index", "NIFTY BANK", "^NSEBANK")):
            repo.upsert_instrument(conn, kind=kind, exchange="NSE", symbol=symbol, name=symbol, yf_symbol=yf)


def option_universe():
    return (nfo_option_rows("NIFTY", date(2026, 10, 27), 25000, 50, 15)
            + nfo_option_rows("NIFTY", date(2026, 11, 3), 25000, 50, 15)
            + nfo_option_rows("BANKNIFTY", date(2026, 10, 27), 56000, 100, 15)
            + nfo_option_rows("BANKNIFTY", date(2026, 11, 24), 56000, 100, 15))


def last_run(engine, job):
    with engine.connect() as conn:
        return repo.last_run_per_job(conn)[job]


def test_unknown_job_is_rejected(engine):
    with pytest.raises(ValueError):
        run_job("place_order", deps(engine), RUN_DAY)


def test_kite_jobs_skip_when_not_logged_in_and_snapshot_records_gap(engine):
    seed(engine)
    assert run_job("sync_instruments", deps(engine), RUN_DAY).status == "skipped"
    result = run_job("snapshot_options", deps(engine), RUN_DAY)
    assert result.status == "skipped" and "not logged in" in result.message
    with engine.connect() as conn:
        issues = repo.recent_issues(conn, None, 10)
    assert [(i["check_name"], i["trade_date"]) for i in issues] == [("missed_snapshot", RUN_DAY)]


def test_sync_stores_tokens_and_only_index_option_contracts(engine):
    seed(engine)
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(),
                                                    "NFO": option_universe() + nfo_noise_rows()})
    result = run_job("sync_instruments", deps(engine, kite), RUN_DAY)
    assert result.status == "ok"
    with engine.connect() as conn:
        live = repo.live_option_contracts(conn, RUN_DAY)
        tokens = {i["symbol"]: i["kite_token"] for i in repo.active_instruments(conn)}
    assert {c.underlying for _, c in live} == {"NIFTY", "BANKNIFTY"}
    assert all(c.option_type in ("CE", "PE") for _, c in live)
    assert len(live) == 4 * 31 * 2
    assert tokens["INFY"] == 408065 and tokens["NIFTY 50"] == 256265 and tokens["TCS"] is None


def test_snapshot_selects_atm_window_for_two_expiries(engine):
    seed(engine)
    rows = option_universe()
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(), "NFO": rows},
                           quotes={**index_quotes(25010.0, 56040.0), **option_quotes(rows)})
    run_job("sync_instruments", deps(engine, kite), RUN_DAY)
    result = run_job("snapshot_options", deps(engine, kite), RUN_DAY)
    assert result.status == "ok"
    assert result.counts["snapshots"] == 2 * 2 * 21 * 2
    with engine.connect() as conn:
        bid, ask = conn.execute(select(option_snapshots.c.bid, option_snapshots.c.ask).limit(1)).one()
    assert (float(bid), float(ask)) == (120.0, 121.0)


def test_snapshot_partial_when_quotes_missing(engine):
    seed(engine)
    rows = option_universe()
    some_quotes = dict(list(option_quotes(rows).items())[::2])
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(), "NFO": rows},
                           quotes={**index_quotes(25010.0, 56040.0), **some_quotes})
    run_job("sync_instruments", deps(engine, kite), RUN_DAY)
    assert run_job("snapshot_options", deps(engine, kite), RUN_DAY).status == "partial"


def test_auth_failure_mid_job_fails_cleanly_and_revokes(engine):
    seed(engine)
    calls = []
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows()}, auth_fail_on={"instruments"})
    result = run_job("sync_instruments", deps(engine, kite, auth_errors=calls), RUN_DAY)
    assert result.status == "failed" and "log in again" in result.message
    assert calls == [1]
    assert last_run(engine, "sync_instruments")["status"] == "failed"


def bars_for(days, close=100.0):
    return [Bar(date(2026, 10, d), close - 1, close + 1, close - 2, close, 1000) for d in days]


def test_ingest_is_idempotent_and_reports_empty_and_failed_symbols(engine):
    seed(engine)
    data = {"INFY.NS": (bars_for([21]), []), "^NSEI": (bars_for([21]), []),
            "^NSEBANK": RuntimeError("yahoo down"), "TCS.NS": ([], [])}
    first = run_job("ingest_daily_bars", deps(engine, bars=data), RUN_DAY)
    assert first.status == "partial"
    assert first.counts == {"instruments": 4, "inserted": 2, "updated": 0, "empty": 1, "failed": 1}
    assert "TCS: empty" in first.message and "NIFTY BANK: RuntimeError" in first.message
    second = run_job("ingest_daily_bars", deps(engine, bars=data), RUN_DAY)
    assert second.counts["inserted"] == 0 and second.counts["updated"] == 0
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(daily_bars)).scalar_one() == 2


def test_backfill_skips_instruments_already_covered(engine):
    seed(engine)
    full = {sym: (bars_for(range(1, 22)), []) for sym in ("INFY.NS", "TCS.NS", "^NSEI", "^NSEBANK")}
    source_deps = deps(engine, bars=full)
    run_job("backfill_history", source_deps, RUN_DAY, years=0)
    calls_after_first = len(source_deps.bar_source.calls)
    second = run_job("backfill_history", source_deps, RUN_DAY, years=0)
    assert second.counts["skipped"] == 4
    assert len(source_deps.bar_source.calls) == calls_after_first


def test_quality_checks_are_replaced_not_duplicated(engine):
    seed(engine)
    bad = [Bar(date(2026, 10, 20), 100, 99, 98, 100, 10), *bars_for([21])]   # high < open/close on 20th
    data = {"INFY.NS": (bad, []), "TCS.NS": (bars_for([20, 21]), []),
            "^NSEI": (bars_for([20, 21]), []), "^NSEBANK": (bars_for([20, 21]), [])}
    d = deps(engine, bars=data)
    run_job("ingest_daily_bars", d, RUN_DAY, start=date(2026, 10, 20))
    run_job("run_quality_checks", d, RUN_DAY)
    run_job("run_quality_checks", d, RUN_DAY)
    with engine.connect() as conn:
        issues = repo.recent_issues(conn, "error", 50)
    assert [(i["check_name"], i["symbol"], i["trade_date"]) for i in issues] == [
        ("ohlc_invalid", "INFY", date(2026, 10, 20))]


def test_unknown_closed_day_detected_instead_of_missing_bars(engine):
    seed(engine)
    days = [19, 21]  # no bars anywhere on Tuesday 20th
    data = {s: (bars_for(days), []) for s in ("INFY.NS", "TCS.NS", "^NSEI", "^NSEBANK")}
    d = deps(engine, bars=data)
    run_job("ingest_daily_bars", d, RUN_DAY, start=date(2026, 10, 19))
    run_job("run_quality_checks", d, RUN_DAY)
    with engine.connect() as conn:
        checks = {(i["check_name"], i["trade_date"]) for i in repo.recent_issues(conn, None, 50)}
    assert ("unknown_closed_day", date(2026, 10, 20)) in checks
    assert not any(c == "missing_bar" for c, _ in checks)


def deps_at(engine, now, kite=None, bars=None):
    return JobDeps(engine=engine, kite=lambda: kite, bar_source=FakeBarSource(bars or {}),
                   calendar=TradingCalendar(), now=lambda: now)


def test_ingest_never_stores_an_unfinished_session_and_refetches_trailing_days(engine):
    seed(engine)
    d = deps_at(engine, datetime(2026, 10, 21, 11, 0, tzinfo=IST))  # market still open on the 21st
    run_job("ingest_daily_bars", d, RUN_DAY)
    run_job("backfill_history", d, RUN_DAY, years=0)
    calls = d.bar_source.calls
    assert {end for _, _, end in calls} == {date(2026, 10, 20)}
    assert date(2026, 10, 14) in {start for _, start, _ in calls}  # 5 trading days: 14,15,16,19,20


def test_one_bad_instrument_write_does_not_abort_the_run(engine):
    seed(engine)
    overflow = [Bar(date(2026, 10, 21), 1e12, 1e12, 1e12, 1e12, 1)]  # exceeds NUMERIC(14,4)
    data = {"INFY.NS": (overflow, []), "TCS.NS": (bars_for([21]), []),
            "^NSEI": (bars_for([21]), []), "^NSEBANK": (bars_for([21]), [])}
    result = run_job("ingest_daily_bars", deps(engine, bars=data), RUN_DAY)
    assert result.status == "partial" and result.counts["inserted"] == 3 and result.counts["failed"] == 1


def test_snapshot_failure_from_expired_session_records_missed_snapshot(engine):
    seed(engine)
    rows = option_universe()
    run_job("sync_instruments", deps(engine, FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(),
                                                                                     "NFO": rows})), RUN_DAY)
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(), "NFO": rows}, auth_fail_on={"quote"})
    assert run_job("snapshot_options", deps(engine, kite), RUN_DAY).status == "failed"
    with engine.connect() as conn:
        checks = [(i["check_name"], i["trade_date"]) for i in repo.recent_issues(conn, None, 10)]
    assert checks == [("missed_snapshot", RUN_DAY)]


def test_snapshot_syncs_contracts_itself_when_not_synced_today(engine):
    seed(engine)
    rows = option_universe()
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(), "NFO": rows},
                           quotes={**index_quotes(25010.0, 56040.0), **option_quotes(rows)})
    result = run_job("snapshot_options", deps(engine, kite), RUN_DAY)
    assert result.status == "ok" and result.counts["snapshots"] == 2 * 2 * 21 * 2


def test_quality_flags_trading_days_without_snapshots_after_collection_started(engine):
    seed(engine)
    rows = option_universe()
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(), "NFO": rows},
                           quotes={**index_quotes(25010.0, 56040.0), **option_quotes(rows)})
    d19 = deps_at(engine, datetime(2026, 10, 19, 15, 35, tzinfo=IST), kite=kite)
    run_job("snapshot_options", d19, date(2026, 10, 19))
    run_job("run_quality_checks", deps(engine), RUN_DAY)
    run_job("run_quality_checks", deps(engine), RUN_DAY)
    with engine.connect() as conn:
        missed = [i["trade_date"] for i in repo.recent_issues(conn, None, 50) if i["check_name"] == "missed_snapshot"]
    assert sorted(missed) == [date(2026, 10, 20), date(2026, 10, 21)]


def test_backfill_defaults_to_ten_years(engine):
    seed(engine)
    d = deps(engine)
    run_job("backfill_history", d, RUN_DAY)
    assert d.bar_source.calls
    assert all(start == date(2016, 10, 21) for _, start, _ in d.bar_source.calls)


def test_scan_history_flags_unadjusted_jumps_across_all_years(engine):
    seed(engine)
    with engine.begin() as conn:
        iid = repo.instrument_by_symbol(conn, "INFY")["id"]
        bars = [Bar(date(2017, 1, 2), 100, 101, 99, 100, 10), Bar(date(2017, 1, 3), 100, 101, 89, 90, 10),
                Bar(date(2017, 1, 4), 90, 91, 89, 90, 10), Bar(date(2017, 1, 5), 22.5, 23, 22, 22.5, 40)]
        repo.upsert_daily_bars(conn, iid, bars, source="yfinance", price_basis="split_adjusted")
    for _ in range(2):  # re-running replaces, never duplicates
        assert run_job("scan_history", deps(engine), RUN_DAY).status == "ok"
    with engine.connect() as conn:
        flags = repo.history_jump_flags(conn)
    assert [(f["instrument_id"], f["trade_date"]) for f in flags] == [(iid, date(2017, 1, 5))]
    assert "-75.0%" in flags[0]["detail"]
