from datetime import date, datetime

from aitrader import scheduler
from aitrader.auth import IST
from aitrader.calendar import TradingCalendar
from aitrader.jobs import JobDeps


def make_deps(now):
    return JobDeps(engine=None, kite=lambda: None, bar_source=None, calendar=TradingCalendar(), now=lambda: now)


def test_weekend_runs_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "run_job", lambda *a, **k: called.append(a))
    assert scheduler.run_if_trading_day("ingest_daily_bars", make_deps(datetime(2026, 10, 24, 16, 30,
                                                                                 tzinfo=IST))) is None
    assert called == []


def test_trading_day_runs_job_for_today(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "run_job", lambda name, deps, day: called.append((name, day)) or "done")
    deps = make_deps(datetime(2026, 10, 21, 16, 30, tzinfo=IST))
    assert scheduler.run_if_trading_day("ingest_daily_bars", deps) == "done"
    assert called == [("ingest_daily_bars", date(2026, 10, 21))]


def test_catch_up_fills_trading_days_since_last_success(monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "_last_ingest_date", lambda deps: date(2026, 10, 15))
    monkeypatch.setattr(scheduler, "run_job", lambda name, deps, day: ran.append(day))
    deps = make_deps(datetime(2026, 10, 21, 10, 0, tzinfo=IST))  # before 16:30: today not final yet
    assert scheduler.catch_up(deps) == [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)]
    assert ran == [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)]


def test_catch_up_without_history_does_nothing(monkeypatch):
    monkeypatch.setattr(scheduler, "_last_ingest_date", lambda deps: None)
    assert scheduler.catch_up(make_deps(datetime(2026, 10, 21, 18, 0, tzinfo=IST))) == []


def test_scheduler_registers_four_weekday_jobs():
    sched = scheduler.build_scheduler(make_deps(datetime(2026, 10, 21, 9, 0, tzinfo=IST)))
    assert sorted(job.id for job in sched.get_jobs()) == sorted(name for name, _, _ in scheduler.SCHEDULE)


def test_last_ingest_date_is_newest_stored_bar_including_backfill(engine):
    from aitrader.db import repo
    from aitrader.models import Bar
    from fakes import FakeBarSource

    with engine.begin() as conn:
        iid = repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol="INFY", name="INFY",
                                     yf_symbol="INFY.NS")
        repo.upsert_daily_bars(conn, iid, [Bar(date(2026, 10, 16), 1, 2, 1, 2, 1)], source="yfinance",
                               price_basis="split_adjusted")
    deps = JobDeps(engine=engine, kite=lambda: None, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: datetime(2026, 10, 21, 18, 0, tzinfo=IST))
    assert scheduler._last_ingest_date(deps) == date(2026, 10, 16)
