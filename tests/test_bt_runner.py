import pandas as pd
import pytest

from aitrader.backtest import runner
from aitrader.backtest.runner import (
    NO_DATA, BacktestBusyError, BacktestInputError, BacktestRequest, create_run, execute_run,
)
from aitrader.backtest.types import MoneyRules
from aitrader.db import repo
from aitrader.models import Bar
from bt_helpers import flat


def seed(engine, series):
    n = max(len(rows) for rows in series.values())
    days = pd.bdate_range("2024-01-01", periods=n)
    with engine.begin() as conn:
        for symbol, rows in series.items():
            kind = "index" if symbol.startswith("NIFTY") else "stock"
            iid = repo.upsert_instrument(conn, kind=kind, exchange="NSE", symbol=symbol, name=symbol,
                                         yf_symbol=f"{symbol}.NS")
            repo.upsert_daily_bars(conn, iid, [Bar(d.date(), r[0], r[1], r[2], r[3], int(r[4]))
                                               for d, r in zip(days, rows)],
                                   source="yfinance", price_basis="split_adjusted")
    return days


def breakout_series():
    return (flat(250) + [(100.5, 103.5, 100, 103, 6e6), (103.2, 104, 102.5, 103.8, 2e6),
                         (103.8, 105.5, 103.5, 105, 2e6), (105, 107.5, 104.8, 107, 2e6),
                         (107, 109.5, 106.8, 109, 2e6), (109, 111.5, 108.8, 111, 2e6),
                         (111, 112.5, 110, 112, 2e6)] + [(111, 111.5, 110.5, 111, 2e6)] * 23)


def test_breakout_end_to_end(engine):
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    days = seed(engine, series)
    run_id = create_run(engine, BacktestRequest("breakout"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        run = repo.get_backtest_run(conn, run_id)
    assert run["status"] == "ok", run["message"]
    assert run["start_date"] == days[199].date() and run["end_date"] == days[279].date()
    (t,) = run["trades"]
    assert (t["symbol"], t["shares"], t["exit_reason"], t["period"]) == ("BRK", 67, "target", "unseen")
    assert float(t["entry_price"]) == pytest.approx(103.2516, abs=1e-3)
    assert float(t["exit_price"]) == pytest.approx((103.2516 + 2 * (103.2516 - (101 - 29.5 / 14))) * 0.9995,
                                                    abs=1e-3)
    assert len(run["equity"]) == 81
    assert float(run["equity"][-1]["equity"]) == pytest.approx(20000 + float(t["pnl"]), abs=0.01)
    assert run["summary"]["whole"]["trades"] == 1 and run["summary"]["index_signals"] == []
    assert run["index_signals"] == []


def test_run_with_empty_database_fails_with_guidance(engine):
    run_id = create_run(engine, BacktestRequest("momentum"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        row = repo.backtest_run_row(conn, run_id)
    assert (row["status"], row["message"]) == ("failed", NO_DATA)


def test_run_without_stocks_fails_with_guidance(engine):
    seed(engine, {"NIFTY 50": flat(210, volume=0)})
    run_id = create_run(engine, BacktestRequest("pullback"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        row = repo.backtest_run_row(conn, run_id)
    assert (row["status"], row["message"]) == ("failed", NO_DATA)


def test_start_before_warm_up_is_rejected(engine):
    days = seed(engine, {"NIFTY 50": flat(210, volume=0)})
    with pytest.raises(BacktestInputError, match="200 days"):
        create_run(engine, BacktestRequest("pullback", start=days[0].date()))


@pytest.mark.parametrize("req", [
    BacktestRequest("magic"),
    BacktestRequest("pullback", money=MoneyRules(risk_per_trade=50_000)),
    BacktestRequest("pullback", money=MoneyRules(max_positions=0)),
    BacktestRequest("pullback", money=MoneyRules(capital=-1)),
    BacktestRequest("pullback", start=pd.Timestamp("2025-01-02").date(), end=pd.Timestamp("2025-01-01").date()),
])
def test_bad_requests_are_rejected(engine, req):
    with pytest.raises(BacktestInputError):
        create_run(engine, req)


def test_second_run_while_one_is_running_is_refused(engine):
    create_run(engine, BacktestRequest("pullback"))
    with pytest.raises(BacktestBusyError):
        create_run(engine, BacktestRequest("breakout"))


def test_unexpected_error_marks_run_failed(engine, monkeypatch):
    seed(engine, {"NIFTY 50": flat(210, volume=0), "AAA": flat(210)})
    run_id = create_run(engine, BacktestRequest("pullback"))

    def boom(*args, **kwargs):
        raise ZeroDivisionError("x")

    monkeypatch.setattr(runner, "simulate", boom)
    execute_run(engine, run_id)
    with engine.connect() as conn:
        row = repo.backtest_run_row(conn, run_id)
    assert (row["status"], row["message"]) == ("failed", "backtest failed: ZeroDivisionError")


def test_flags_and_scan_status_are_disclosed_in_the_summary(engine):
    from aitrader.models import Issue
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series(), "FL1": flat(280), "FL2": flat(280)}
    days = seed(engine, series)
    run_id = create_run(engine, BacktestRequest("breakout"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        never_scanned = repo.get_backtest_run(conn, run_id)["summary"]["warnings"]
    assert any("price-jump scan has not run" in w for w in never_scanned)

    with engine.begin() as conn:
        fl1, fl2 = (repo.instrument_by_symbol(conn, s)["id"] for s in ("FL1", "FL2"))
        job = repo.start_run(conn, "scan_history", days[-1].date())
        repo.insert_issues(conn, job, [
            Issue("history_adjustment", "error", days[230].date(), "overnight gap -75.0% ≈ ×4", fl1),
            Issue("history_jump", "warn", days[240].date(), "close-to-open move -40.0%", fl2)])
        repo.finish_run(conn, job, "ok", {})
    run_id = create_run(engine, BacktestRequest("breakout"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        run = repo.get_backtest_run(conn, run_id)
    assert run["status"] == "ok", run["message"]
    assert run["summary"]["data_flags"] == [
        {"symbol": "FL1", "date": days[230].date().isoformat(), "kind": "adjustment",
         "detail": "overnight gap -75.0% ≈ ×4"},
        {"symbol": "FL2", "date": days[240].date().isoformat(), "kind": "large_move",
         "detail": "close-to-open move -40.0%"}]
    warnings = run["summary"]["warnings"]
    assert not any("price-jump scan has not run" in w for w in warnings)
    assert any("1 look like unadjusted splits/bonuses" in w and "1 look like real moves" in w for w in warnings)
    assert [t["symbol"] for t in run["trades"]] == ["BRK"]


def test_price_action_runs_end_to_end(engine):
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    seed(engine, series)
    run_id = create_run(engine, BacktestRequest("price_action",
                                                money=MoneyRules(risk_per_trade=300, daily_loss_limit=600)))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        run = repo.get_backtest_run(conn, run_id)
    assert run["status"] == "ok", run["message"]
    assert "index_signals" in run["summary"] and run["params"]["money"]["risk_per_trade"] == 300


def test_missing_rbi_file_warns(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RBI_DATES_PATH", tmp_path / "none.yaml")
    seed(engine, {"NIFTY 50": flat(210, volume=0), "AAA": flat(210)})
    run_id = create_run(engine, BacktestRequest("price_action"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        run = repo.get_backtest_run(conn, run_id)
    assert run["status"] == "ok", run["message"]
    assert any("RBI policy dates missing" in w for w in run["summary"]["warnings"])
