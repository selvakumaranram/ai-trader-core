from datetime import date

from aitrader.db import repo
from aitrader.models import Bar, CorporateAction, Issue, OptionContract


def b(day, c=100.0):
    return Bar(date(2026, 10, day), c - 1, c + 1, c - 2, c, 1000)


def make_stock(conn, symbol="INFY"):
    return repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol=symbol, name=symbol,
                                  yf_symbol=f"{symbol}.NS")


def test_upsert_instrument_is_stable(engine):
    with engine.begin() as conn:
        first = make_stock(conn)
        second = make_stock(conn)
    assert first == second


def test_daily_bars_idempotent_and_detect_real_changes(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        assert repo.upsert_daily_bars(conn, iid, [b(1), b(2)], source="yfinance",
                                      price_basis="split_adjusted") == (2, 0)
        assert repo.upsert_daily_bars(conn, iid, [b(1), b(2)], source="yfinance",
                                      price_basis="split_adjusted") == (0, 0)
        assert repo.upsert_daily_bars(conn, iid, [b(2, c=50.0)], source="yfinance",
                                      price_basis="split_adjusted") == (0, 1)
        bars = repo.load_bars(conn, iid, source="yfinance")
    assert [x.close for x in bars] == [100.0, 50.0]


def test_duplicate_dates_in_one_batch_keep_the_last(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        repo.upsert_daily_bars(conn, iid, [b(1, c=10.0), b(1, c=11.0)], source="yfinance",
                               price_basis="split_adjusted")
        assert [x.close for x in repo.load_bars(conn, iid, source="yfinance")] == [11.0]


def test_option_contracts_keep_first_seen(engine):
    c = OptionContract("NIFTY", date(2026, 10, 27), 25000.0, "CE", 75, "NIFTY26102725000CE", 1)
    with engine.begin() as conn:
        repo.upsert_option_contracts(conn, [c], seen_on=date(2026, 10, 20))
        repo.upsert_option_contracts(conn, [c], seen_on=date(2026, 10, 21))
        live = repo.live_option_contracts(conn, date(2026, 10, 21))
        assert [x for _, x in live] == [c]
        assert repo.live_option_contracts(conn, date(2026, 10, 28)) == []


def test_runs_and_issues(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        run_id = repo.start_run(conn, "ingest_daily_bars", date(2026, 10, 20))
        repo.finish_run(conn, run_id, "ok", {"bars": 2})
        repo.insert_issues(conn, run_id, [Issue("missing_bar", "warn", date(2026, 10, 20), "x", iid)])
        assert repo.last_run_date(conn, "ingest_daily_bars") == date(2026, 10, 20)
        assert repo.issue_counts(conn) == {"warn": 1}
        assert repo.recent_issues(conn, None, 10)[0]["symbol"] == "INFY"
        assert repo.delete_issues(conn, ["missing_bar"], date(2026, 10, 1), date(2026, 10, 31)) == 1
        assert repo.last_run_per_job(conn)["ingest_daily_bars"]["status"] == "ok"


def test_coverage_and_actions(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        repo.upsert_daily_bars(conn, iid, [b(1), b(2)], source="yfinance", price_basis="split_adjusted")
        repo.upsert_corporate_actions(conn, iid, [CorporateAction(date(2026, 10, 2), "split", 1, 2)],
                                      source="yfinance")
        cov = repo.coverage(conn)
        assert cov["bars"]["stock"]["instruments"] == 1
        assert cov["bars"]["stock"]["first_date"] == date(2026, 10, 1)
        assert repo.action_dates(conn, iid) == [date(2026, 10, 2)]
        assert repo.price_basis_of(conn, iid, "yfinance") == "split_adjusted"
        assert repo.days_with_any_bar(conn, date(2026, 10, 1), date(2026, 10, 31), "yfinance") == {
            date(2026, 10, 1), date(2026, 10, 2)}
