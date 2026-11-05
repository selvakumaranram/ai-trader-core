from __future__ import annotations

import secrets
import time
from datetime import date
from typing import Literal, Callable, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from aitrader.auth import SessionStore
from aitrader.backtest.report import equity_csv, file_stem, report_markdown, trades_csv
from aitrader.backtest.runner import (
    INTERRUPTED, SOURCE, BacktestBusyError, BacktestInputError, BacktestRequest, create_run, execute_run,
)
from aitrader.backtest.types import NEW_RUN_MONEY, MoneyRules
from aitrader.db import repo
from aitrader.jobs import JOB_NAMES, JobDeps, run_job
from aitrader.kite.gateway import KiteAuthError, KiteGateway

STATE_TTL_SECONDS = 600
NOT_CONFIGURED = "Kite is not configured: set KITE_API_KEY, KITE_API_SECRET and TOKEN_ENCRYPTION_KEY in .env"

_DEFAULT_MONEY = NEW_RUN_MONEY


class ShadowViewBody(BaseModel):
    view: Optional[Literal["agree", "disagree"]] = None


class BacktestBody(BaseModel):
    strategy: str
    start: Optional[date] = None
    end: Optional[date] = None
    capital: float = _DEFAULT_MONEY.capital
    risk_per_trade: float = _DEFAULT_MONEY.risk_per_trade
    daily_loss_limit: float = _DEFAULT_MONEY.daily_loss_limit
    max_positions: int = _DEFAULT_MONEY.max_positions
    max_position_value: float = _DEFAULT_MONEY.max_position_value
    risk_mode: str = _DEFAULT_MONEY.risk_mode
    risk_pct: float = _DEFAULT_MONEY.risk_pct
    daily_loss_pct: Optional[float] = _DEFAULT_MONEY.daily_loss_pct
    regime_filter: bool = _DEFAULT_MONEY.regime_filter
    max_charge_to_risk: Optional[float] = _DEFAULT_MONEY.max_charge_to_risk
    max_drawdown_pause: Optional[float] = _DEFAULT_MONEY.max_drawdown_pause
    pause_min_days: int = _DEFAULT_MONEY.pause_min_days


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
        money = MoneyRules(**body.model_dump(exclude={"strategy", "start", "end"}))
        try:
            run_id = create_run(deps.engine, BacktestRequest(body.strategy, body.start, body.end, money))
        except BacktestInputError as exc:
            raise HTTPException(400, str(exc)) from None
        except BacktestBusyError as exc:
            raise HTTPException(409, str(exc)) from None
        background.add_task(execute_run, deps.engine, run_id)
        return {"id": run_id}

    @app.get("/local/shadow")
    def shadow_overview():
        from aitrader.shadow.live import scoreboard
        with deps.engine.connect() as conn:
            signals = repo.shadow_signals_list(conn)
            trades = repo.shadow_trades_list(conn)
        return {"signals": signals, "trades": trades, "scoreboard": scoreboard(signals, trades)}

    @app.post("/local/shadow/signals/{signal_id}/view")
    def shadow_view(signal_id: int, body: ShadowViewBody):
        with deps.engine.begin() as conn:
            if not repo.set_shadow_view(conn, signal_id, body.view):
                raise HTTPException(404, f"No shadow signal #{signal_id}")
        return {"id": signal_id, "view": body.view}

    @app.get("/local/shadow/replay")
    def shadow_replay():
        from aitrader.backtest.bars import frame_from_bars
        from aitrader.backtest.indicators import add_indicators
        from aitrader.shadow.rules import replay
        with deps.engine.connect() as conn:
            inst = repo.instrument_by_symbol(conn, "NIFTY 50")
            bars = repo.load_bars(conn, inst["id"], source="yfinance") if inst else []
        if len(bars) < 300:
            raise HTTPException(409, "Load NIFTY 50 history first (Data tab)")
        df = add_indicators(frame_from_bars(bars))
        last = df.index[-1].date()
        unseen_from = last.replace(year=last.year - 2)
        return {"from": df.index[200].date(), "to": last, "unseen_from": unseen_from,
                "rules": replay(df.iloc[200:], unseen_from)}

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

    def _finished_run(run_id: int) -> dict:
        with deps.engine.connect() as conn:
            run = repo.get_backtest_run(conn, run_id)
        if run is None:
            raise HTTPException(404, f"No backtest {run_id}")
        if run["status"] != "ok":
            raise HTTPException(400, "This backtest has no results to download")
        return run

    def _download(body: str, filename: str, media_type: str, encoding: str = "utf-8") -> Response:
        return Response(body.encode(encoding), media_type=f"{media_type}; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/local/backtests/{run_id}/report.md")
    def backtest_report(run_id: int):
        run = _finished_run(run_id)
        return _download(report_markdown(run), f"{file_stem(run)}.md", "text/markdown")

    @app.get("/local/backtests/{run_id}/trades.csv")
    def backtest_trades_csv(run_id: int):
        run = _finished_run(run_id)  # utf-8-sig so Excel shows ₹ and other symbols correctly
        return _download(trades_csv(run), f"{file_stem(run)}-trades.csv", "text/csv", "utf-8-sig")

    @app.get("/local/backtests/{run_id}/equity.csv")
    def backtest_equity_csv(run_id: int):
        run = _finished_run(run_id)
        return _download(equity_csv(run), f"{file_stem(run)}-equity.csv", "text/csv", "utf-8-sig")

    return app
