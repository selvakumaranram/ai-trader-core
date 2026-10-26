import csv
import io
from datetime import datetime

from fastapi.testclient import TestClient

from aitrader.api import create_app
from aitrader.auth import IST
from aitrader.backtest.report import equity_csv, report_markdown, trades_csv
from aitrader.backtest.runner import BacktestRequest, create_run, execute_run
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.jobs import JobDeps
from bt_helpers import flat
from fakes import FakeBarSource
from test_bt_runner import breakout_series, seed


def finished_run(engine):
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    seed(engine, series)
    run_id = create_run(engine, BacktestRequest("breakout"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        return repo.get_backtest_run(conn, run_id)


def test_report_markdown_explains_rules_results_and_caveats(engine):
    md = report_markdown(finished_run(engine))
    assert md.startswith("# Backtest report — C — Breakout (run #")
    for text in ("not a promise of future profit", "## 1. What the strategy does", "20-day high",
                 "## 3. Results", "| Trades | — | 1 | 1 |", "Where the money went", "Survivorship bias",
                 "## 5. Questions to study"):
        assert text in md, text


def test_trades_and_equity_csv(engine):
    run = finished_run(engine)
    rows = list(csv.DictReader(io.StringIO(trades_csv(run))))
    assert len(rows) == 1 and rows[0]["symbol"] == "BRK" and rows[0]["exit_reason"] == "target"
    assert rows[0]["reason"].startswith("Closed at 103.00")
    equity = list(csv.DictReader(io.StringIO(equity_csv(run))))
    assert len(equity) == 81 and set(equity[0]) == {"date", "cash", "invested_value", "equity",
                                                    "nifty50_buy_and_hold"}


def client(engine):
    deps = JobDeps(engine=engine, kite=lambda: None, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: datetime(2026, 10, 21, 17, 0, tzinfo=IST))
    return TestClient(create_app(deps=deps, sessions=None, gateway_factory=None, dashboard_url="http://x"))


def test_download_endpoints(engine):
    run = finished_run(engine)
    c = client(engine)
    md = c.get(f"/local/backtests/{run['id']}/report.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert f'filename="backtest-{run["id"]}-breakout.md"' in md.headers["content-disposition"]
    trades = c.get(f"/local/backtests/{run['id']}/trades.csv")
    assert trades.status_code == 200 and trades.headers["content-type"].startswith("text/csv")
    assert "BRK" in trades.text
    assert c.get(f"/local/backtests/{run['id']}/equity.csv").status_code == 200


def test_download_unknown_or_unfinished_run(engine):
    c = client(engine)
    assert c.get("/local/backtests/999/report.md").status_code == 404
    with engine.begin() as conn:
        run_id = repo.insert_backtest_run(conn, strategy="pullback", start_date=None, end_date=None,
                                          unseen_from=None, params={})
        repo.finish_backtest_run(conn, run_id, status="failed", message="x")
    assert c.get(f"/local/backtests/{run_id}/trades.csv").status_code == 400


def test_report_lists_flag_types(engine):
    run = finished_run(engine)
    run["summary"]["data_flags"] = [
        {"symbol": "AAA", "date": "2024-01-01", "kind": "adjustment", "detail": "overnight gap -75.0% ≈ ×4"},
        {"symbol": "BBB", "date": "2020-03-06", "kind": "large_move", "detail": "close-to-close move -56.1%"}]
    md = report_markdown(run)
    assert "| AAA | 2024-01-01 | Likely unadjusted split/bonus |" in md
    assert "| BBB | 2020-03-06 | Large real move |" in md
