"""Shadow trading for C3's stock signals: evening scan, next-morning pretend buy, trailing stop, exits. No orders."""
from datetime import date, datetime

import pandas as pd
import pytest

from aitrader.auth import IST
from aitrader.backtest.costs import CostModel
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.jobs import JobDeps, run_job
from aitrader.kite.fake import FakeKiteGateway
from aitrader.models import Bar
from fakes import FakeBarSource

RUN_DAY = date(2026, 10, 21)  # Wednesday
NEXT_DAY = date(2026, 10, 22)


def deps(engine, kite=None, now=datetime(2026, 10, 21, 17, 0, tzinfo=IST)):
    return JobDeps(engine=engine, kite=lambda: kite, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: now)


def seed(engine, breakout=True):
    days = [d.date() for d in pd.bdate_range(end=pd.Timestamp(RUN_DAY), periods=260)]
    nifty = [Bar(d, 20000 + k * 10, 20010 + k * 10, 19990 + k * 10, 20000 + k * 10, 0) for k, d in enumerate(days)]
    stock = [Bar(d, 1000, 1005, 995, 1000, 200_000) for d in days[:-1]]
    last = Bar(days[-1], 1001, 1040, 1000, 1035, 900_000) if breakout else Bar(days[-1], 1000, 1005, 995, 1000, 200_000)
    stock.append(last)
    with engine.begin() as conn:
        nid = repo.upsert_instrument(conn, kind="index", exchange="NSE", symbol="NIFTY 50", name="NIFTY 50",
                                     yf_symbol="^NSEI")
        repo.upsert_daily_bars(conn, nid, nifty, source="yfinance", price_basis="split_adjusted")
        sid = repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol="ACME", name="ACME", yf_symbol="ACME.NS")
        repo.upsert_daily_bars(conn, sid, stock, source="yfinance", price_basis="split_adjusted")
    return sid


def stock_signals(engine):
    with engine.connect() as conn:
        return [s for s in repo.shadow_signals_list(conn) if s["rule"] == "c3_stock"]


def test_scan_records_a_c3_breakout_as_a_stock_signal(engine):
    seed(engine)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    (s,) = stock_signals(engine)
    assert (s["underlying"], s["side"], s["signal_date"], s["target"]) == ("ACME", "BUY", RUN_DAY, None)
    assert float(s["stop"]) < 1035 and s["facts"]["atr14"] > 0


def test_no_signal_without_a_breakout(engine):
    seed(engine, breakout=False)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    assert stock_signals(engine) == []


def entered(engine, price=1020.0):
    seed(engine)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    kite = FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": 22600.0}, "NSE:ACME": {"last_price": price}})
    run_job("shadow_enter", deps(engine, kite, datetime(2026, 10, 22, 9, 20, tzinfo=IST)), NEXT_DAY)
    with engine.connect() as conn:
        return [t for t in repo.shadow_trades_list(conn) if t["kind"] == "stock"], kite


def test_enter_buys_shares_sized_to_one_percent_risk_of_a_one_lakh_account(engine):
    (t,), kite = entered(engine)
    (s,) = stock_signals(engine)
    risk = 1020.0 - float(s["stop"])
    assert t["lot_size"] == min(int(1000 // risk), int(25_000 // 1020.0))
    assert float(t["entry_price"]) == 1020.0 and float(t["initial_stop"]) == float(s["stop"])
    assert t["tradingsymbol"] == "ACME" and t["expiry"] is None and set(kite.calls) == {"quote"}


def test_exit_on_the_stop_with_delivery_charges_and_r(engine):
    (t,), _ = entered(engine)
    stop = float(t["stop"])
    kite = FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": 22600.0}, "NSE:ACME": {"last_price": stop - 5}})
    run_job("shadow_exit", deps(engine, kite, datetime(2026, 10, 22, 15, 25, tzinfo=IST)), NEXT_DAY)
    with engine.connect() as conn:
        (t,) = [x for x in repo.shadow_trades_list(conn) if x["kind"] == "stock"]
    shares, entry, exit_ = t["lot_size"], float(t["entry_price"]), stop - 5
    costs = CostModel()
    charges = costs.buy_charges(shares * entry) + costs.sell_charges(shares * exit_)
    assert (t["status"], t["exit_reason"]) == ("closed", "stop")
    assert float(t["pnl_per_lot"]) == pytest.approx((exit_ - entry) * shares - charges, abs=0.05)
    risk = shares * (entry - float(t["initial_stop"]))
    assert float(t["r_multiple"]) == pytest.approx(float(t["pnl_per_lot"]) / risk, abs=0.01)


def test_trailing_stop_rises_with_closes_and_never_falls(engine):
    (t,), _ = entered(engine)
    with engine.connect() as conn:
        sid = repo.instrument_by_symbol(conn, "ACME")["id"]
    start = float(t["stop"])
    for d, close in ((NEXT_DAY, 1100.0), (date(2026, 10, 23), 1080.0)):
        with engine.begin() as conn:
            repo.upsert_daily_bars(conn, sid, [Bar(d, close, close + 5, close - 5, close, 200_000)], source="yfinance",
                                   price_basis="split_adjusted")
        run_job("shadow_scan", deps(engine, now=datetime(d.year, d.month, d.day, 17, 0, tzinfo=IST)), d)
    with engine.connect() as conn:
        (t,) = [x for x in repo.shadow_trades_list(conn) if x["kind"] == "stock"]
    assert float(t["stop"]) > start and float(t["highest_close"]) == 1100.0
