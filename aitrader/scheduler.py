from __future__ import annotations

import logging
from datetime import date, time, timedelta
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from aitrader.db import repo
from aitrader.jobs import JobDeps, RunResult, run_job

log = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"
SCHEDULE = (("sync_instruments", 8, 30), ("snapshot_options", 15, 35),
            ("ingest_daily_bars", 16, 30), ("run_quality_checks", 16, 45))
CATCH_UP_MAX_DAYS = 30
DAILY_DATA_FINAL_AT = time(16, 30)


def run_if_trading_day(name: str, deps: JobDeps) -> Optional[RunResult]:
    today = deps.now().date()
    if not deps.calendar.is_trading_day(today):
        log.info("skipping %s: %s is not a trading day", name, today)
        return None
    return run_job(name, deps, today)


def _last_ingest_date(deps: JobDeps) -> Optional[date]:
    with deps.engine.connect() as conn:
        return repo.last_run_date(conn, "ingest_daily_bars")


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


def build_scheduler(deps: JobDeps) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=TIMEZONE)
    for name, hour, minute in SCHEDULE:
        sched.add_job(run_if_trading_day, CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute,
                                                      timezone=TIMEZONE),
                      args=[name, deps], id=name, max_instances=1, coalesce=True, misfire_grace_time=3600)
    return sched
