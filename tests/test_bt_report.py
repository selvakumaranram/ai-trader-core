import csv
import io
from datetime import datetime

from fastapi.testclient import TestClient

from aitrader.api import create_app
from aitrader.auth import IST
from aitrader.backtest.report import equity_csv, report_markdown, trades_csv
from aitrader.backtest.runner import BacktestRequest, create_run, execute_run
from aitrader.backtest.types import MoneyRules
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


def price_action_run(engine):
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    seed(engine, series)
    run_id = create_run(engine, BacktestRequest("price_action"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        return repo.get_backtest_run(conn, run_id)


def test_report_for_price_action_explains_rules(engine):
    md = report_markdown(price_action_run(engine))
    for text in ("D — Price action", "higher highs", "1.5R", "breakeven", "RBI"):
        assert text in md, text


def test_report_shows_loss_streak_notice(engine):
    run = finished_run(engine)
    run["summary"]["loss_streaks"] = {
        "whole": {"alerts": 3, "after_trades": 5, "after_win_rate": 0.2, "after_avg_r": -0.4},
        "unseen": {"alerts": 0, "after_trades": 0, "after_win_rate": None, "after_avg_r": None}}
    md = report_markdown(run)
    assert "2 losses in a row happened 3 times" in md and "-0.40R" in md and "not paused" in md


def test_price_action_report_never_states_the_fixed_2r_exit(engine):
    md = report_markdown(price_action_run(engine))
    assert "after 20 trading days" not in md and "+2R = target hit" not in md
    assert "| Target (× risk) | 2 |" not in md and "| Time stop (trading days) | 20 |" not in md
    assert "| Time stop (trading days) | 30 |" in md and "| Partial profit at (× risk) | 1.5 |" in md


def strategy_run(engine, strategy, money=None):
    from aitrader.backtest.types import NEW_RUN_MONEY
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    seed(engine, series)
    run_id = create_run(engine, BacktestRequest(strategy, money=money or NEW_RUN_MONEY))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        return repo.get_backtest_run(conn, run_id)


def test_report_shows_new_money_rules_and_edge_tables(engine):
    run = strategy_run(engine, "breakout")
    assert run["status"] == "ok"
    run["summary"]["drawdown_pauses"] = [{"start_date": "2025-01-02", "resume_date": "2025-01-20",
                                          "equity_at_pause": 85000.0, "equity_at_resume": 86000.0,
                                          "days_paused": 12}]
    run["summary"]["skips"] = {"regime_off": 4, "drawdown_pause": 2, "charges_too_high": 1}
    md = report_markdown(run)
    for text in ("| Risk per trade | 1.0% of equity |", "| Daily loss stop | 3.0% of equity |",
                 "| Market regime filter (NIFTY 50 vs 200-day avg) | On |", "| Max charges ÷ risk | 15.0% |",
                 "| Drawdown pause | 15.0% below peak, min 10 trading days |", "Gross R | Charges R | Net R",
                 "By stop distance (measured from the actual entry price)", "By market regime on the signal day",
                 "Trades cut below the intended risk", "1 pause(s), 12 trading days paused in total, 2 signals",
                 "regime off = NIFTY 50 closed at or below its 200-day average", "Survivorship bias",
                 "not a promise of future profit"):
        assert text in md, text


def test_old_settings_report_says_off(engine):
    md = report_markdown(strategy_run(engine, "breakout", money=MoneyRules()))
    assert "| Risk per trade | ₹400 |" in md and "| Market regime filter (NIFTY 50 vs 200-day avg) | Off |" in md
    assert "| Max charges ÷ risk | Off |" in md and "| Drawdown pause | Off |" in md


def test_a2_and_b2_run_end_to_end_and_explain_their_rules(engine):
    a2 = strategy_run(engine, "momentum_a2")
    assert a2["status"] == "ok", a2["message"]
    md = report_markdown(a2)
    for text in ("A2 — Medium-term momentum", "close[t−21] ÷ close[t−126]", "Rank exit", "No fixed target",
                 "| Trailing stop (× ATR14 below highest close) | 3.0 |", "| Time stop (trading days) | 60 |"):
        assert text in md, text
    assert "+2R = target hit" not in md and "| Target (× risk) |" not in md


def test_b2_report_marks_index_signals_research_only(engine):
    b2 = strategy_run(engine, "pullback_b2")
    assert b2["status"] == "ok", b2["message"]
    b2["summary"]["index_signals"] = [{"symbol": "NIFTY 50", "direction": "long", "period": "earlier",
                                       "signals": 3, "win_rate": 0.33, "avg_r": 0.1}]
    md = report_markdown(b2)
    for text in ("B2 — Trend pullback, volatility-based stop", "1.5 × ATR(14)", "126-day return",
                 "not for trading, research only", "| Stop distance at entry (× ATR14) | 1.0–3.0 |"):
        assert text in md, text


def test_report_shows_checklist_na_charge_spread_and_entry_gaps(engine):
    run = strategy_run(engine, "breakout_c2")
    assert run["status"] == "ok", run["message"]
    run["summary"]["checklist"] = [
        {"key": "unseen_trades", "label": "At least 30 trades in the unseen period", "passed": False},
        {"key": "positive_expectancy", "label": "Positive expectancy", "passed": None, "na": True}]
    run["summary"]["charge_check"] = {"taken": {"count": 2, "min": 0.04, "median": 0.06, "max": 0.08},
                                      "skipped": {"count": 3, "min": 0.2, "median": 0.3, "max": 0.9}}
    md = report_markdown(run)
    for text in ("C2 — Breakout, entry-checked, trailing exit", "n/a — not enough trades to judge",
                 "Estimated charges ÷ risk", "| Taken | 2 | 4.0% | 6.0% | 8.0% |",
                 "| Skipped by the charge check | 3 | 20.0% | 30.0% | 90.0% |", "### By entry gap",
                 "| Time stop (trading days) | 40 |", "| Stop distance at entry (× ATR14) | 1.0–3.0 |",
                 "no stop-distance cap at the signal", "Trailing stop (× ATR14 below highest close) | 3.0 |"):
        assert text in md, text
    assert "Rank exit" not in md and "| Target (× risk) |" not in md
