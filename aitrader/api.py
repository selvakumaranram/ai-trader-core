from __future__ import annotations

import secrets
import time
from datetime import date
from typing import Callable, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from aitrader.auth import SessionStore
from aitrader.backtest.runner import (
    INTERRUPTED, SOURCE, BacktestBusyError, BacktestInputError, BacktestRequest, create_run, execute_run,
)
from aitrader.backtest.types import MoneyRules
from aitrader.db import repo
from aitrader.jobs import JOB_NAMES, JobDeps, run_job
from aitrader.kite.gateway import KiteAuthError, KiteGateway

STATE_TTL_SECONDS = 600
NOT_CONFIGURED = "Kite is not configured: set KITE_API_KEY, KITE_API_SECRET and TOKEN_ENCRYPTION_KEY in .env"

_DEFAULT_MONEY = MoneyRules()


class BacktestBody(BaseModel):
    strategy: str
    start: Optional[date] = None
    end: Optional[date] = None
    capital: float = _DEFAULT_MONEY.capital
    risk_per_trade: float = _DEFAULT_MONEY.risk_per_trade
    daily_loss_limit: float = _DEFAULT_MONEY.daily_loss_limit
    max_positions: int = _DEFAULT_MONEY.max_positions
    max_position_value: float = _DEFAULT_MONEY.max_position_value


def create_app(*, deps: JobDeps, sessions: Optional[SessionStore],
               gateway_factory: Optional[Callable[[Optional[str]], KiteGateway]], dashboard_url: str,
               clock: Callable[[], float] = time.monotonic) -> FastAPI:
    app = FastAPI(title="aitrader local service", docs_url=None, redoc_url=None, openapi_url=None)
    pending_states: Dict[str, float] = {}
    with deps.engine.begin() as conn:
        repo.fail_interrupted_backtests(conn, INTERRUPTED)

    def kite_status() -> dict:
        if sessions is None:
            return {"configured": False, "connected": False, "kite_user_id": None, "expires_at": None}
        return sessions.status()

    @app.get("/local/status")
    def status():
        with deps.engine.connect() as conn:
            return {"coverage": repo.coverage(conn), "last_runs": repo.last_run_per_job(conn),
                    "issue_counts": repo.issue_counts(conn), "kite": kite_status()}

    @app.get("/local/kite/status")
    def kite_status_endpoint():
        return kite_status()

    @app.get("/local/kite/login")
    def kite_login():
        if sessions is None or gateway_factory is None:
            raise HTTPException(503, NOT_CONFIGURED)
        now = clock()
        for stale in [s for s, expiry in pending_states.items() if expiry < now]:
            pending_states.pop(stale, None)
        state = secrets.token_urlsafe(24)
        pending_states[state] = now + STATE_TTL_SECONDS
        return RedirectResponse(gateway_factory(None).login_url(state), status_code=302)

    @app.get("/local/kite/callback")
    def kite_callback(background: BackgroundTasks, request_token: str = "", status: str = "",
                      state: str = ""):
        if sessions is None or gateway_factory is None:
            raise HTTPException(503, NOT_CONFIGURED)
        expiry = pending_states.pop(state, None) if state else None
        if expiry is None or expiry < clock():
            raise HTTPException(400, "Invalid or expired login state — start the login again")
        if status != "success" or not request_token:
            raise HTTPException(400, "Kite login was not completed")
        try:
            session = gateway_factory(None).exchange_request_token(request_token)
        except KiteAuthError:
            raise HTTPException(400, "Kite rejected the login — start the login again") from None
        sessions.save(session)
        background.add_task(run_job, "sync_instruments", deps, deps.now().date())
        return RedirectResponse(f"{dashboard_url}/?kite=connected", status_code=302)

    @app.post("/local/jobs/{job}/run", status_code=202)
    def run(job: str, background: BackgroundTasks):
        if job not in JOB_NAMES:
            raise HTTPException(404, f"Unknown job {job!r}")
        background.add_task(run_job, job, deps, deps.now().date())
        return {"accepted": job}

    @app.get("/local/runs")
    def runs(limit: int = Query(20, ge=1, le=200)):
        with deps.engine.connect() as conn:
            return repo.recent_runs(conn, limit)

    @app.get("/local/issues")
    def issues(severity: Optional[str] = Query(None, pattern="^(info|warn|error)$"),
               limit: int = Query(50, ge=1, le=500)):
        with deps.engine.connect() as conn:
            return repo.recent_issues(conn, severity, limit)

    @app.post("/local/backtests", status_code=202)
    def start_backtest(body: BacktestBody, background: BackgroundTasks):
        money = MoneyRules(capital=body.capital, risk_per_trade=body.risk_per_trade,
                           daily_loss_limit=body.daily_loss_limit, max_positions=body.max_positions,
                           max_position_value=body.max_position_value)
        try:
            run_id = create_run(deps.engine, BacktestRequest(body.strategy, body.start, body.end, money))
        except BacktestInputError as exc:
            raise HTTPException(400, str(exc)) from None
        except BacktestBusyError as exc:
            raise HTTPException(409, str(exc)) from None
        background.add_task(execute_run, deps.engine, run_id)
        return {"id": run_id}

    @app.get("/local/backtests")
    def list_backtests(limit: int = Query(20, ge=1, le=200)):
        with deps.engine.connect() as conn:
            return repo.list_backtest_runs(conn, limit)

    @app.get("/local/backtests/{run_id}")
    def get_backtest(run_id: int):
        with deps.engine.connect() as conn:
            run = repo.get_backtest_run(conn, run_id)
        if run is None:
            raise HTTPException(404, f"No backtest {run_id}")
        return run

    @app.get("/local/bars/{symbol}")
    def bars(symbol: str, start: Optional[date] = None, end: Optional[date] = None):
        with deps.engine.connect() as conn:
            rows = repo.daily_bars_for_symbol(conn, symbol, source=SOURCE, start=start, end=end)
        if rows is None:
            raise HTTPException(404, f"Unknown symbol {symbol!r}")
        return rows

    return app
