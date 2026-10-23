from datetime import date, datetime

from fastapi.testclient import TestClient

from aitrader.api import create_app
from aitrader.auth import IST
from aitrader.backtest.runner import INTERRUPTED, NO_DATA
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.jobs import JobDeps
from aitrader.models import Bar
from fakes import FakeBarSource


def client(engine):
    deps = JobDeps(engine=engine, kite=lambda: None, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: datetime(2026, 10, 21, 17, 0, tzinfo=IST))
    return TestClient(create_app(deps=deps, sessions=None, gateway_factory=None, dashboard_url="http://x"))


def running_run(engine):
    with engine.begin() as conn:
        return repo.insert_backtest_run(conn, strategy="pullback", start_date=None, end_date=None,
                                        unseen_from=None, params={})


def test_run_lifecycle_without_data(engine):
    c = client(engine)
    response = c.post("/local/backtests", json={"strategy": "pullback"})
    assert response.status_code == 202
    run_id = response.json()["id"]
    body = c.get(f"/local/backtests/{run_id}").json()
    assert (body["status"], body["message"]) == ("failed", NO_DATA)
    assert c.get("/local/backtests").json()[0]["id"] == run_id


def test_bad_input_is_400(engine):
    c = client(engine)
    assert c.post("/local/backtests", json={"strategy": "magic"}).status_code == 400
    assert c.post("/local/backtests", json={"strategy": "pullback", "risk_per_trade": 50000}).status_code == 400
    assert c.post("/local/backtests", json={"strategy": "pullback", "capital": "lots"}).status_code == 422


def test_second_run_is_409(engine):
    c = client(engine)
    running_run(engine)
    assert c.post("/local/backtests", json={"strategy": "breakout"}).status_code == 409


def test_unknown_run_is_404(engine):
    assert client(engine).get("/local/backtests/999").status_code == 404


def test_interrupted_runs_fail_on_startup(engine):
    run_id = running_run(engine)
    body = client(engine).get(f"/local/backtests/{run_id}").json()
    assert (body["status"], body["message"]) == ("failed", INTERRUPTED)


def test_bars_endpoint(engine):
    with engine.begin() as conn:
        iid = repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol="INFY", name="Infosys",
                                     yf_symbol="INFY.NS")
        repo.upsert_daily_bars(conn, iid, [Bar(date(2024, 1, 2), 1, 2, 0.5, 1.5, 10),
                                           Bar(date(2024, 1, 3), 1.5, 2, 1, 1.8, 12)],
                               source="yfinance", price_basis="split_adjusted")
    c = client(engine)
    rows = c.get("/local/bars/INFY", params={"start": "2024-01-03"}).json()
    assert rows == [{"trade_date": "2024-01-03", "open": 1.5, "high": 2.0, "low": 1.0, "close": 1.8, "volume": 12}]
    assert c.get("/local/bars/NOPE").status_code == 404
