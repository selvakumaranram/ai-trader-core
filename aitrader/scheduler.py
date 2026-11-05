from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from aitrader.db import repo
from aitrader.jobs import JobDeps, run_job

log = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"
SCHEDULE = (("sync_instruments", 8, 30), ("shadow_enter", 9, 20), ("shadow_exit", 15, 25),
            ("snapshot_options", 15, 35), ("ingest_daily_bars", 16, 30), ("shadow_scan", 16, 40),
            ("run_quality_checks", 16, 45), ("ai_picks", 17, 0))
NEEDS_LOGIN = {"sync_instruments", "snapshot_options", "shadow_enter", "shadow_exit"}
CATCH_UP_MAX_DAYS = 30
DAILY_DATA_FINAL_AT = time(16, 30)


def _last_ingest_date(deps: JobDeps) -> Optional[date]:
    with deps.engine.connect() as conn:
        return repo.latest_bar_date(conn, deps.bar_source.name)


def catch_up(deps: JobDeps) -> List[date]:
    last = _last_ingest_date(deps)
    if last is None:
        return []  # nothing loaded yet: the user runs "Load history" (backfill_history) first
    now = deps.now()
    today = now.date()
    final_through = today if now.time() >= DAILY_DATA_FINAL_AT else today - timedelta(days=1)
    start = max(last + timedelta(days=1), today - timedelta(days=CATCH_UP_MAX_DAYS))
    days = deps.calendar.trading_days(start, final_through)
    for day in days:
        run_job("ingest_daily_bars", deps, day)
    return days


def _statuses_since(deps: JobDeps, name: str, day: date, since: datetime) -> List[str]:
    with deps.engine.connect() as conn:
        return repo.run_statuses_since(conn, name, day, since)


def _done(deps: JobDeps, name: str, statuses: List[str]) -> bool:
    """A run since the job's time counts, except a not-logged-in skip once Kite is connected again."""
    if not statuses:
        return False
    if name in NEEDS_LOGIN and all(s == "skipped" for s in statuses):
        return deps.kite() is None
    return True


def catch_up_today(deps: JobDeps) -> List[str]:
    """Run, in order, each of today's jobs whose time has passed and that has not run since then.

    Checked every minute instead of firing once at the exact time: on Windows a sleeping laptop pauses the
    scheduler's timers, so one-shot daily triggers fire hours late or not at all.
    """
    now = deps.now()
    today = now.date()
    if not deps.calendar.is_trading_day(today):
        return []
    ran = []
    for name, hour, minute in SCHEDULE:
        due_at = datetime.combine(today, time(hour, minute), tzinfo=now.tzinfo)
        if now < due_at or _done(deps, name, _statuses_since(deps, name, today, due_at)):
            continue
        log.info("running %s for %s (due %s)", name, today, due_at.time())
        run_job(name, deps, today)
        ran.append(name)
    return ran


def build_scheduler(deps: JobDeps) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=TIMEZONE)
    sched.add_job(catch_up_today, IntervalTrigger(minutes=1, timezone=TIMEZONE), args=[deps],
                  id="catch_up_today", max_instances=1, coalesce=True, misfire_grace_time=None)
    return sched
