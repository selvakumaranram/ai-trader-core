"""Your-picks shadow portfolio (only signals you agree with) and backtest statistics for each stock signal."""
from datetime import date, datetime

import pytest

from aitrader.auth import IST
from aitrader.db import repo
from aitrader.jobs import run_job
from aitrader.kite.fake import FakeKiteGateway
from aitrader.shadow import stats as S
from aitrader.shadow.live import scoreboard
from test_shadow_stocks import NEXT_DAY, RUN_DAY, deps, seed, stock_signals

MORNING = datetime(2026, 10, 22, 9, 20, tzinfo=IST)


# --- statistics -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("surge, bucket", [(1.5, "1.5–2×"), (1.99, "1.5–2×"), (2.0, "2–3×"), (2.99, "2–3×"),
                                           (3.0, "3× or more"), (7.0, "3× or more"), (None, None)])
def test_volume_bucket(surge, bucket):
    assert S.volume_bucket(surge) == bucket


def test_group_stats_counts_wins_average_r_and_big_winners():
    g = S.group_stats([-1.0, -1.0, 2.0, 4.0])
    assert g == {"n": 4, "win_rate": 0.5, "avg_r": 1.0, "big_winners": 0.25}
    assert S.group_stats([]) == {"n": 0, "win_rate": None, "avg_r": None, "big_winners": None}


def past(n, r, surge, sector):
    return [{"r_multiple": r, "facts": {"volume_surge": surge, "sector": sector}} for _ in range(n)]


def test_signal_stats_gives_all_same_volume_and_same_sector_groups_with_sample_warnings():
    trades = past(40, 1.0, 2.5, "Finance") + past(20, -1.0, 1.6, "Finance") + past(10, 3.5, 2.2, "IT")
    groups = S.signal_stats({"volume_surge": 2.4}, "Finance", trades)
    assert [g["label"] for g in groups] == ["All C3 breakouts", "Volume 2–3× normal", "Sector: Finance"]
    assert [g["n"] for g in groups] == [70, 50, 60]
    assert groups[1]["avg_r"] == pytest.approx((40 * 1.0 + 10 * 3.5) / 50)
    assert groups[0]["note"] == ""
    it = S.signal_stats({"volume_surge": 2.4}, "IT", trades)[2]
    assert it["n"] == 10 and it["win_rate"] is None and "too few" in it["note"]
    mid = S.signal_stats({"volume_surge": 2.4}, "IT", trades + past(10, 1.0, 2.0, "IT"))[2]
    assert mid["n"] == 20 and mid["win_rate"] is not None and "small sample" in mid["note"]


def test_signal_stats_without_a_sector_skips_that_group():
    groups = S.signal_stats({"volume_surge": 2.4}, None, past(40, 1.0, 2.5, "Finance"))
    assert [g["label"] for g in groups] == ["All C3 breakouts", "Volume 2–3× normal"]


def finished_run(engine, strategy="breakout_c3", overrides=None, exclude=(), status="ok"):
    with engine.begin() as conn:
        rid = repo.insert_backtest_run(conn, strategy=strategy, start_date=date(2017, 8, 14),
                                       end_date=date(2026, 10, 30), unseen_from=date(2024, 10, 30),
                                       params={"strategy_overrides": overrides or {}, "exclude": list(exclude)})
        repo.finish_backtest_run(conn, rid, status=status, summary={})
    return rid


def test_stats_come_from_the_latest_plain_c3_run(engine):
    plain = finished_run(engine)
    finished_run(engine, overrides={"failed_breakout_days": 5})
    finished_run(engine, exclude=("ADANIENT",))
    finished_run(engine, strategy="momentum_a2")
    finished_run(engine, status="failed")
    with engine.connect() as conn:
        assert repo.latest_plain_run(conn, "breakout_c3")["id"] == plain


# --- your picks portfolio -------------------------------------------------------------------------------------

def enter(engine, view=None, price=1020.0):
    seed(engine)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    (s,) = stock_signals(engine)
    if view:
        with engine.begin() as conn:
            repo.set_shadow_view(conn, s["id"], view)
    kite = FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": 22600.0}, "NSE:ACME": {"last_price": price}})
    run_job("shadow_enter", deps(engine, kite, MORNING), NEXT_DAY)
    with engine.connect() as conn:
        trades = [t for t in repo.shadow_trades_list(conn) if t["kind"] == "stock"]
    return trades, stock_signals(engine)[0]


def test_an_agreed_signal_is_bought_in_both_the_rule_and_your_picks_portfolios(engine):
    trades, s = enter(engine, view="agree")
    assert sorted(t["portfolio"] for t in trades) == ["mine", "rule"]
    a, b = trades
    assert (a["lot_size"], a["entry_price"], a["stop"]) == (b["lot_size"], b["entry_price"], b["stop"])
    assert s["status"] == "entered" and s["pick_note"] == "bought"


@pytest.mark.parametrize("view", [None, "disagree"])
def test_without_agree_only_the_rule_portfolio_buys(engine, view):
    trades, s = enter(engine, view=view)
    assert [t["portfolio"] for t in trades] == ["rule"] and s["pick_note"] is None


def test_your_picks_has_its_own_five_slots(engine):
    seed(engine)
    with engine.begin() as conn:  # the rule portfolio is already full
        for k in range(5):
            sid = repo.insert_shadow_signal(conn, signal_date=date(2026, 10, 1), rule="c3_stock", side="BUY",
                                            close=100, stop=90, target=None, reason="t", facts={},
                                            underlying=f"OLD{k}")
            repo.insert_shadow_trade(conn, signal_id=sid, rule="c3_stock", side="BUY", kind="stock",
                                     tradingsymbol=f"OLD{k}", lot_size=1, entry_at=MORNING, entry_price=100,
                                     underlying_entry=100, stop=90, initial_stop=90, highest_close=100, target=0)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    (s,) = [x for x in stock_signals(engine) if x["underlying"] == "ACME"]
    with engine.begin() as conn:
        repo.set_shadow_view(conn, s["id"], "agree")
    kite = FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": 22600.0}, "NSE:ACME": {"last_price": 1020.0}})
    run_job("shadow_enter", deps(engine, kite, MORNING), NEXT_DAY)
    with engine.connect() as conn:
        acme = [t for t in repo.shadow_trades_list(conn) if t["tradingsymbol"] == "ACME"]
    (s,) = [x for x in stock_signals(engine) if x["underlying"] == "ACME"]
    assert [t["portfolio"] for t in acme] == ["mine"]
    assert s["status"] == "skipped" and "no free slot" in s["note"] and s["pick_note"] == "bought"


def test_scoreboard_keeps_the_rule_counts_clean_and_adds_both_portfolios():
    sig = {"id": 1, "rule": "c3_stock", "status": "entered", "user_view": "agree"}
    trades = [{"signal_id": 1, "rule": "c3_stock", "portfolio": p, "status": "closed", "pnl_per_lot": pnl,
               "r_multiple": r} for p, pnl, r in (("rule", 500.0, 1.0), ("mine", 600.0, 1.2))]
    board = scoreboard([sig], trades)
    assert board["rules"]["c3_stock"]["closed"] == 1 and board["views"]["agree"]["closed"] == 1
    assert board["portfolios"]["rule"]["total_pnl"] == 500.0 and board["portfolios"]["mine"]["total_pnl"] == 600.0
    assert board["portfolios"]["mine"]["avg_r"] == pytest.approx(1.2)
    assert board["portfolios"]["mine"]["signals"] == 1


def test_api_adds_backtest_statistics_to_each_stock_signal(engine):
    from test_shadow import client
    rid = finished_run(engine)
    row = dict(symbol="X", signal_date=date(2020, 1, 1), entry_date=date(2020, 1, 2), entry_price=100, shares=1,
               stop=90, target=None, exit_date=date(2020, 2, 1), exit_price=120, exit_reason="trail_stop",
               charges=1, pnl=19, reason="t", period="earlier")
    with engine.begin() as conn:
        repo.insert_backtest_trades(conn, rid, [{**row, "r_multiple": r, "facts": {"volume_surge": v}}
                                                for r, v in [(2.0, 2.5)] * 20 + [(-1.0, 1.6)] * 20])
    seed(engine)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    (s,) = stock_signals(engine)
    body = client(engine).get("/local/shadow").json()
    assert body["stats_source"] == {"run_id": rid, "strategy": "breakout_c3", "from": "2017-08-14",
                                    "to": "2026-10-30", "trades": 40}
    groups = body["signal_stats"][str(s["id"])]
    assert groups[0]["label"] == "All C3 breakouts" and groups[0]["n"] == 40 and groups[0]["win_rate"] == 0.5


def test_api_without_a_c3_run_has_no_statistics(engine):
    from test_shadow import client
    body = client(engine).get("/local/shadow").json()
    assert body["stats_source"] is None and body["signal_stats"] == {}
