from datetime import date, datetime

from aitrader import scheduler
from aitrader.auth import IST
from aitrader.calendar import TradingCalendar
from aitrader.jobs import JobDeps


def make_deps(now):
    return JobDeps(engine=None, kite=lambda: None, bar_source=None, calendar=TradingCalendar(), now=lambda: now)


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


def _fake_runs(monkeypatch, done=None, ran=None):
    """done: {job: [statuses since its time]}; ran collects (job, day) for each run_job call."""
    done = done or {}
    monkeypatch.setattr(scheduler, "_statuses_since", lambda deps, name, day, since: done.get(name, []))
    monkeypatch.setattr(scheduler, "run_job", lambda name, deps, day: ran.append((name, day)))


def test_catch_up_today_runs_every_due_job_in_schedule_order(monkeypatch):
    ran = []
    _fake_runs(monkeypatch, ran=ran)
    deps = make_deps(datetime(2026, 10, 28, 17, 9, tzinfo=IST))  # woke at 17:09 after sleeping through 15:35
    assert scheduler.catch_up_today(deps) == ["sync_instruments", "snapshot_options", "ingest_daily_bars",
                                              "run_quality_checks"]
    assert ran == [(name, date(2026, 10, 28)) for name, _, _ in scheduler.SCHEDULE]


def test_catch_up_today_waits_for_each_jobs_time(monkeypatch):
    ran = []
    _fake_runs(monkeypatch, ran=ran)
    assert scheduler.catch_up_today(make_deps(datetime(2026, 10, 28, 16, 40, tzinfo=IST))) == [
        "sync_instruments", "snapshot_options", "ingest_daily_bars"]


def test_catch_up_today_skips_jobs_already_run_since_their_time(monkeypatch):
    ran = []
    _fake_runs(monkeypatch, done={"sync_instruments": ["ok"], "snapshot_options": ["failed"],
                                  "ingest_daily_bars": ["running"]}, ran=ran)
    assert scheduler.catch_up_today(make_deps(datetime(2026, 10, 28, 16, 50, tzinfo=IST))) == [
        "run_quality_checks"]


def test_catch_up_today_does_nothing_on_a_weekend(monkeypatch):
    ran = []
    _fake_runs(monkeypatch, ran=ran)
    assert scheduler.catch_up_today(make_deps(datetime(2026, 10, 24, 17, 0, tzinfo=IST))) == []
    assert ran == []


def test_snapshot_skipped_for_no_login_is_retried_only_once_logged_in(monkeypatch):
    ran = []
    _fake_runs(monkeypatch, done={"sync_instruments": ["skipped"], "snapshot_options": ["skipped"],
                                  "ingest_daily_bars": ["ok"], "run_quality_checks": ["ok"]}, ran=ran)
    now = datetime(2026, 10, 28, 17, 0, tzinfo=IST)
    assert scheduler.catch_up_today(make_deps(now)) == []  # still not logged in: no new row every minute
    logged_in = JobDeps(engine=None, kite=lambda: object(), bar_source=None, calendar=TradingCalendar(),
                        now=lambda: now)
    assert scheduler.catch_up_today(logged_in) == ["sync_instruments", "snapshot_options"]


def test_scheduler_registers_one_minute_watchdog():
    sched = scheduler.build_scheduler(make_deps(datetime(2026, 10, 21, 9, 0, tzinfo=IST)))
    assert [job.id for job in sched.get_jobs()] == ["catch_up_today"]


def test_statuses_since_ignores_runs_started_before_the_scheduled_time(engine):
    from aitrader.db.schema import ingestion_runs

    day = date(2026, 10, 28)
    with engine.begin() as conn:
        conn.execute(ingestion_runs.insert(), [
            dict(job="ingest_daily_bars", run_date=day, status="ok",
                 started_at=datetime(2026, 10, 28, 10, 0, tzinfo=IST)),  # manual morning run
            dict(job="snapshot_options", run_date=day, status="skipped",
                 started_at=datetime(2026, 10, 28, 15, 36, tzinfo=IST)),
            dict(job="snapshot_options", run_date=date(2026, 10, 27), status="ok",
                 started_at=datetime(2026, 10, 28, 15, 40, tzinfo=IST))])
    deps = JobDeps(engine=engine, kite=lambda: None, bar_source=None, calendar=TradingCalendar(),
                   now=lambda: datetime(2026, 10, 28, 17, 0, tzinfo=IST))
    since_1630 = datetime(2026, 10, 28, 16, 30, tzinfo=IST)
    since_1535 = datetime(2026, 10, 28, 15, 35, tzinfo=IST)
    assert scheduler._statuses_since(deps, "ingest_daily_bars", day, since_1630) == []
    assert scheduler._statuses_since(deps, "snapshot_options", day, since_1535) == ["skipped"]


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
