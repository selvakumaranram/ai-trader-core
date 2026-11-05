"""Shadow trading: candidate rules, replay, daily scan / pretend entry / pretend exit, user views. No orders."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from aitrader.api import create_app
from aitrader.auth import IST
from aitrader.backtest.indicators import add_indicators
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.jobs import JobDeps, run_job
from aitrader.kite.fake import FakeKiteGateway
from aitrader.models import Bar, OptionContract
from aitrader.shadow import rules as R
from aitrader.shadow.charges import option_round_trip_charges
from bt_helpers import frame
from fakes import FakeBarSource

RUN_DAY = date(2026, 10, 21)  # Wednesday
PREV_DAY = date(2026, 10, 20)


def flat_rows(n, price=100.0):
    return [(price, price + 1, price - 1, price, 0)] * n


def ind(rows):
    return add_indicators(frame(rows))


# --- rules -------------------------------------------------------------------------------------------------------

def test_big_move_up_closing_near_the_high_is_a_ce_signal():
    df = ind(flat_rows(30) + [(100, 102.2, 99.9, 102.0, 0)])  # +2%, close in the top quarter
    (s,) = R.big_move(df)
    assert s.side == "CE" and s.rule == "big_move"
    atr = float(df["atr14"].iloc[-1])
    assert s.stop == pytest.approx(102.0 - atr) and s.target == pytest.approx(102.0 + 2 * atr)


def test_big_move_down_and_mid_range_close():
    (s,) = R.big_move(ind(flat_rows(30) + [(100, 100.1, 97.8, 98.0, 0)]))
    assert s.side == "PE" and s.stop > 98.0 > s.target
    assert R.big_move(ind(flat_rows(30) + [(100, 104.0, 99.0, 102.0, 0)])) == []  # +2% but mid-range


def test_gap_hold():
    (s,) = R.gap_hold(ind(flat_rows(30) + [(101.5, 102.5, 101.2, 102.0, 0)]))  # gap up 1.5%, closes above open
    assert s.side == "CE"
    assert R.gap_hold(ind(flat_rows(30) + [(101.5, 102.0, 100.5, 101.0, 0)])) == []  # gap faded
    (s,) = R.gap_hold(ind(flat_rows(30) + [(98.5, 98.8, 97.5, 98.0, 0)]))
    assert s.side == "PE"


def test_sharp_dip_in_an_uptrend():
    up = [(100 + k * 0.2, 101 + k * 0.2, 99 + k * 0.2, 100 + k * 0.2, 0) for k in range(260)]
    last = up[-1][3]
    df = ind(up + [(last, last, last - 12, last - 11, 0)])
    (s,) = R.sharp_dip(df)
    assert s.side == "CE" and float(df["close"].iloc[-1]) > float(df["sma200"].iloc[-1])
    assert R.sharp_dip(ind(up)) == []


def test_agree2_needs_two_votes_and_the_200_day_side():
    assert R.agree2_side(["B", "C"], [], close=110, sma200=100) == "CE"
    assert R.agree2_side(["B"], [], close=110, sma200=100) is None
    assert R.agree2_side(["B", "C"], [], close=90, sma200=100) is None
    assert R.agree2_side([], ["C", "D"], close=90, sma200=100) == "PE"


def test_every_rule_is_registered():
    assert set(R.RULES) == {"agree2", "big_move", "gap_hold", "sharp_dip"}


def test_replay_reports_earlier_and_unseen_per_rule():
    rows = flat_rows(30)
    for k in range(40):  # a +2% day closing near its high every 10 days (rows 30, 40, 50, 60), flat otherwise
        rows += [(100.5, 102.8, 100.4, 102.7, 0)] if k % 10 == 0 else flat_rows(1, 100.5)
    df = ind(rows)
    out = R.replay(df, unseen_from=df.index[50].date(), rules=["big_move"])
    stats = out["big_move"]
    assert set(stats) == {"earlier", "unseen"}
    assert (stats["earlier"]["signals"], stats["unseen"]["signals"]) == (2, 2)
    assert stats["earlier"]["right_direction"] == 0.0  # the price fell back to 100.5 each time


# --- charges -----------------------------------------------------------------------------------------------------

def test_option_round_trip_charges():
    buy, sell = 100 * 75, 120 * 75
    exchange = 0.0003503 * (buy + sell)
    sebi = 10 / 1e7 * (buy + sell)
    expected = 40 + 0.001 * sell + exchange + sebi + 0.00003 * buy + 0.18 * (40 + exchange + sebi)
    assert option_round_trip_charges(100, 120, 75) == pytest.approx(expected)


# --- jobs --------------------------------------------------------------------------------------------------------

def deps(engine, kite=None, now=datetime(2026, 10, 21, 17, 0, tzinfo=IST)):
    return JobDeps(engine=engine, kite=lambda: kite, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: now)


def seed_nifty(engine, last_bar):
    days = [d.date() for d in pd.bdate_range(end=pd.Timestamp(RUN_DAY), periods=31)]
    bars = [Bar(d, 24000, 24100, 23900, 24000, 0) for d in days[:-1]]
    o, h, low, c = last_bar
    bars.append(Bar(days[-1], o, h, low, c, 0))
    with engine.begin() as conn:
        iid = repo.upsert_instrument(conn, kind="index", exchange="NSE", symbol="NIFTY 50", name="NIFTY 50",
                                     yf_symbol="^NSEI")
        repo.upsert_daily_bars(conn, iid, bars, source="yfinance", price_basis="split_adjusted")


BIG_UP = (24000, 24560, 23990, 24500)  # +2.1%, close in the top quarter


def test_scan_stores_signals_once_and_waits_for_the_days_bar(engine):
    seed_nifty(engine, BIG_UP)
    assert run_job("shadow_scan", deps(engine), RUN_DAY).counts["signals"] >= 1
    run_job("shadow_scan", deps(engine), RUN_DAY)
    with engine.connect() as conn:
        sigs = repo.shadow_signals_list(conn)
    big = [s for s in sigs if s["rule"] == "big_move"]
    assert len(big) == 1 and big[0]["side"] == "CE" and big[0]["signal_date"] == RUN_DAY
    assert run_job("shadow_scan", deps(engine), RUN_DAY + timedelta(days=1)).status == "skipped"


def nifty_contracts():
    out = []
    for expiry in (RUN_DAY + timedelta(days=2), RUN_DAY + timedelta(days=9)):
        for strike in range(24000, 24250, 50):
            for t in ("CE", "PE"):
                out.append(OptionContract("NIFTY", expiry, float(strike), t, 75,
                                          f"NIFTY{expiry:%y%m%d}{strike}{t}", strike * 10 + (t == "PE")))
    return out


def seed_signal(engine, side="CE", day=PREV_DAY, rule="big_move"):
    stop, target = (23980.0, 24280.0) if side == "CE" else (24180.0, 23880.0)
    with engine.begin() as conn:
        repo.upsert_option_contracts(conn, nifty_contracts(), seen_on=PREV_DAY)
        return repo.insert_shadow_signal(conn, signal_date=day, rule=rule, side=side, close=24080.0,
                                         stop=stop, target=target, reason="test", facts={})


def option_kite(side="CE", spot=24080.0, premium=120.0):
    ts = f"NIFTY{RUN_DAY + timedelta(days=9):%y%m%d}24100{side}"
    return FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": spot}, f"NFO:{ts}": {"last_price": premium}}), ts


def at(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def test_enter_buys_one_lot_of_the_atm_option_at_least_a_week_out(engine):
    seed_signal(engine)
    kite, ts = option_kite()
    res = run_job("shadow_enter", deps(engine, kite, at(RUN_DAY, 9, 20)), RUN_DAY)
    assert res.status == "ok" and res.counts["entered"] == 1
    with engine.connect() as conn:
        (t,) = repo.shadow_trades_list(conn)
    assert (t["tradingsymbol"], float(t["entry_price"]), t["lot_size"], t["status"]) == (ts, 120.0, 75, "open")
    assert t["expiry"] == RUN_DAY + timedelta(days=9) and float(t["strike"]) == 24100.0 and not t["note"]
    assert set(kite.calls) == {"quote"}  # quotes only — never an order


def test_enter_without_login_is_skipped_and_late_entries_are_marked(engine):
    seed_signal(engine)
    assert run_job("shadow_enter", deps(engine, None), RUN_DAY).status == "skipped"
    kite, _ = option_kite()
    run_job("shadow_enter", deps(engine, kite, at(RUN_DAY, 11, 5)), RUN_DAY)
    with engine.connect() as conn:
        (t,) = repo.shadow_trades_list(conn)
    assert "late" in t["note"]


def test_scan_marks_yesterdays_unentered_signals_missed(engine):
    seed_nifty(engine, BIG_UP)
    sid = seed_signal(engine)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    with engine.connect() as conn:
        assert next(s for s in repo.shadow_signals_list(conn) if s["id"] == sid)["status"] == "missed"


def open_trade(engine, side="CE", premium=120.0):
    seed_signal(engine, side=side)
    kite, ts = option_kite(side, premium=premium)
    run_job("shadow_enter", deps(engine, kite, at(RUN_DAY, 9, 20)), RUN_DAY)
    return ts


def exit_kite(ts, spot, premium):
    return FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": spot}, f"NFO:{ts}": {"last_price": premium}})


def test_exit_on_target_with_pnl_after_charges(engine):
    ts = open_trade(engine)
    run_job("shadow_exit", deps(engine, exit_kite(ts, 24300.0, 190.0), at(RUN_DAY, 15, 25)), RUN_DAY)
    with engine.connect() as conn:
        (t,) = repo.shadow_trades_list(conn)
    assert (t["status"], t["exit_reason"], float(t["exit_price"])) == ("closed", "target", 190.0)
    assert float(t["pnl_per_lot"]) == pytest.approx((190 - 120) * 75 - option_round_trip_charges(120, 190, 75),
                                                    abs=0.01)


def test_exit_marks_an_open_trade_then_closes_on_time(engine):
    ts = open_trade(engine)
    run_job("shadow_exit", deps(engine, exit_kite(ts, 24100.0, 125.0), at(RUN_DAY, 15, 25)), RUN_DAY)
    with engine.connect() as conn:
        (t,) = repo.shadow_trades_list(conn)
    assert t["status"] == "open" and float(t["mark_price"]) == 125.0
    day6 = date(2026, 10, 28)  # 5 trading days after the 21st
    run_job("shadow_exit", deps(engine, exit_kite(ts, 24100.0, 110.0), at(day6, 15, 25)), day6)
    with engine.connect() as conn:
        (t,) = repo.shadow_trades_list(conn)
    assert (t["status"], t["exit_reason"]) == ("closed", "time")


def test_exit_on_stop_for_a_pe(engine):
    ts = open_trade(engine, side="PE", premium=100.0)
    run_job("shadow_exit", deps(engine, exit_kite(ts, 24200.0, 60.0), at(RUN_DAY, 15, 25)), RUN_DAY)  # stop 24180
    with engine.connect() as conn:
        (t,) = repo.shadow_trades_list(conn)
    assert (t["status"], t["exit_reason"]) == ("closed", "stop")


def test_exit_before_expiry(engine):
    ts = open_trade(engine)
    expiry_eve = RUN_DAY + timedelta(days=8)  # Thu 29 Oct, the day before the 30 Oct expiry
    run_job("shadow_exit", deps(engine, exit_kite(ts, 24100.0, 80.0), at(expiry_eve, 15, 25)), expiry_eve)
    with engine.connect() as conn:
        (t,) = repo.shadow_trades_list(conn)
    assert (t["status"], t["exit_reason"]) == ("closed", "expiry")


def test_scheduler_runs_the_shadow_jobs():
    from aitrader import scheduler
    names = [n for n, _, _ in scheduler.SCHEDULE]
    assert {"shadow_enter", "shadow_exit", "shadow_scan"} <= set(names)
    assert names.index("shadow_scan") > names.index("ingest_daily_bars")
    assert {"shadow_enter", "shadow_exit"} <= scheduler.NEEDS_LOGIN


# --- API ---------------------------------------------------------------------------------------------------------

def client(engine):
    return TestClient(create_app(deps=deps(engine), sessions=None, gateway_factory=None, dashboard_url="http://x"))


def test_api_lists_signals_trades_scoreboard_and_records_views(engine):
    sid = seed_signal(engine)
    c = client(engine)
    assert c.post(f"/local/shadow/signals/{sid}/view", json={"view": "agree"}).status_code == 200
    assert c.post(f"/local/shadow/signals/{sid}/view", json={"view": "maybe"}).status_code == 422
    body = c.get("/local/shadow").json()
    assert body["signals"][0]["user_view"] == "agree"
    assert {"rules", "views"} <= set(body["scoreboard"])
    assert c.post("/local/shadow/signals/999999/view", json={"view": "agree"}).status_code == 404
