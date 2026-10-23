from datetime import date

from aitrader.db import repo
from aitrader.models import Bar


def new_run(conn, strategy="pullback"):
    return repo.insert_backtest_run(conn, strategy=strategy, start_date=date(2024, 1, 1), end_date=date(2025, 1, 1),
                                    unseen_from=date(2024, 6, 1), params={"money": {"capital": 20000.0}})


def test_run_lifecycle_and_listing(engine):
    with engine.begin() as conn:
        run_id = new_run(conn)
        assert repo.backtest_running(conn) is True
        repo.insert_backtest_trades(conn, run_id, [dict(
            instrument_id=None, symbol="AAA", signal_date=date(2024, 2, 1), entry_date=date(2024, 2, 2),
            entry_price=100.05, shares=69, stop=95, target=110.15, exit_date=date(2024, 2, 5), exit_price=110.09,
            exit_reason="target", charges=33.2, pnl=650.5, r_multiple=1.86, reason="why", facts={"close": 100.0},
            period="earlier")])
        repo.insert_backtest_equity(conn, run_id, [dict(trade_date=date(2024, 2, 2), cash=13000, invested_value=7000,
                                                        equity=20000, benchmark_equity=20000)])
        repo.insert_backtest_index_signals(conn, run_id, [dict(
            symbol="NIFTY 50", direction="short", signal_date=date(2024, 3, 1), entry_date=date(2024, 3, 4),
            entry=22000, stop=22300, target=21400, exit_date=date(2024, 3, 8), exit=21400, exit_reason="target",
            r_multiple=2.0, reason="why", period="unseen")])
        repo.finish_backtest_run(conn, run_id, status="ok",
                                 summary={"whole": {"return_pct": 0.1}, "checklist": [], "years": [1]})
        assert repo.backtest_running(conn) is False
    with engine.connect() as conn:
        (listed,) = repo.list_backtest_runs(conn, 10)
        assert listed["id"] == run_id and listed["summary"] == {"whole": {"return_pct": 0.1}, "checklist": []}
        full = repo.get_backtest_run(conn, run_id)
        assert full["status"] == "ok" and full["finished_at"] is not None
        assert full["trades"][0]["facts"] == {"close": 100.0} and float(full["trades"][0]["pnl"]) == 650.5
        assert len(full["equity"]) == 1 and full["index_signals"][0]["direction"] == "short"
        assert repo.get_backtest_run(conn, run_id + 99) is None


def test_fail_interrupted(engine):
    with engine.begin() as conn:
        run_id = new_run(conn)
        assert repo.fail_interrupted_backtests(conn, "interrupted") == 1
        row = repo.backtest_run_row(conn, run_id)
        assert (row["status"], row["message"]) == ("failed", "interrupted")


def test_bars_by_symbol_and_dates(engine):
    with engine.begin() as conn:
        iid = repo.upsert_instrument(conn, kind="index", exchange="NSE", symbol="NIFTY 50", name="NIFTY 50",
                                     yf_symbol="^NSEI")
        repo.upsert_daily_bars(conn, iid, [Bar(date(2024, 1, 2), 1, 2, 0.5, 1.5, 0),
                                           Bar(date(2024, 1, 3), 1.5, 2, 1, 1.8, 0)],
                               source="yfinance", price_basis="split_adjusted")
    with engine.connect() as conn:
        assert repo.instrument_by_symbol(conn, "NIFTY 50")["id"] == iid
        assert repo.bar_dates(conn, iid, "yfinance") == [date(2024, 1, 2), date(2024, 1, 3)]
        rows = repo.daily_bars_for_symbol(conn, "NIFTY 50", source="yfinance", start=date(2024, 1, 3))
        assert [r["trade_date"] for r in rows] == [date(2024, 1, 3)] and float(rows[0]["close"]) == 1.8
        assert repo.daily_bars_for_symbol(conn, "NOPE", source="yfinance") is None
