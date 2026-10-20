from __future__ import annotations

import argparse
import sys
import threading
from datetime import date, datetime
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from aitrader.auth import IST, SessionStore
from aitrader.calendar import TradingCalendar
from aitrader.config import Settings
from aitrader.db.engine import make_engine
from aitrader.jobs import JOB_NAMES, JobDeps, run_job
from aitrader.kite.real import RealKiteGateway
from aitrader.redaction import configure_logging
from aitrader.sources.yfinance_source import YFinanceSource

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_runtime(settings: Settings):
    engine = make_engine(settings.database_url)
    sessions = (SessionStore(engine, settings.token_encryption_key.get_secret_value())
                if settings.kite_configured() else None)

    def gateway_factory(access_token):
        return RealKiteGateway(settings.kite_api_key, settings.kite_api_secret.get_secret_value(), access_token)

    def kite():
        active = sessions.active() if sessions else None
        return gateway_factory(active.access_token) if active else None

    deps = JobDeps(engine=engine, kite=kite, bar_source=YFinanceSource(), calendar=TradingCalendar.bundled(),
                   on_auth_error=sessions.revoke if sessions else (lambda: None))
    return deps, sessions, (gateway_factory if sessions else None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m aitrader")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the local API + scheduler on 127.0.0.1")
    sub.add_parser("migrate", help="apply database migrations")
    seed = sub.add_parser("seed-universe", help="load the Nifty 200 list from a CSV you downloaded")
    seed.add_argument("--csv", required=True, type=Path)
    job = sub.add_parser("run-job", help="run one job now")
    job.add_argument("name", choices=JOB_NAMES)
    job.add_argument("--date", type=date.fromisoformat, default=None)
    job.add_argument("--years", type=int, default=5, help="backfill_history only")
    sub.add_parser("live-check", help="read-only Kite round-trip once your plan is active")
    return parser


def _live_check(deps, sessions) -> int:
    if sessions is None:
        print("Kite is not configured: set KITE_API_KEY, KITE_API_SECRET and TOKEN_ENCRYPTION_KEY in .env")
        return 1
    kite = deps.kite()
    if kite is None:
        print("Not logged in: open the dashboard Data tab and click 'Log in to Kite' first.")
        return 1
    print(f"Profile OK: user {kite.profile().get('user_id')}")
    nse = kite.instruments("NSE")
    nifty = next((r for r in nse if r["tradingsymbol"] == "NIFTY 50"), None)
    print(f"NSE instruments: {len(nse)}")
    print(f"NIFTY 50 last price: {kite.quote(['NSE:NIFTY 50'])['NSE:NIFTY 50']['last_price']}")
    if nifty:
        today = datetime.now(IST).date()
        candles = kite.historical_daily(nifty["instrument_token"], date(today.year, 1, 1), today)
        print(f"NIFTY 50 daily candles this year: {len(candles)}; last close {candles[-1]['close'] if candles else '-'}")
        print("Compare a few closes with yfinance (^NSEI) to confirm Kite's price basis before trusting Kite bars.")
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging([settings.kite_api_secret.get_secret_value(), settings.token_encryption_key.get_secret_value()])

    if args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        command.upgrade(cfg, "head")
        return 0

    deps, sessions, gateway_factory = build_runtime(settings)

    if args.command == "seed-universe":
        from aitrader.universe import seed_universe

        counts = seed_universe(deps.engine, args.csv.read_text(encoding="utf-8"), as_of=datetime.now(IST).date())
        print(counts)
        return 0
    if args.command == "run-job":
        kwargs = {"years": args.years} if args.name == "backfill_history" else {}
        result = run_job(args.name, deps, args.date or datetime.now(IST).date(), **kwargs)
        print(f"{args.name}: {result.status} {result.counts} {result.message}")
        return 0 if result.status in ("ok", "partial", "skipped") else 1
    if args.command == "live-check":
        return _live_check(deps, sessions)

    # serve
    from aitrader.api import create_app
    from aitrader.scheduler import build_scheduler, catch_up

    app = create_app(deps=deps, sessions=sessions, gateway_factory=gateway_factory,
                     dashboard_url=settings.dashboard_url)
    sched = build_scheduler(deps)
    sched.start()
    threading.Thread(target=catch_up, args=(deps,), daemon=True, name="catch-up").start()
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    finally:
        sched.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
