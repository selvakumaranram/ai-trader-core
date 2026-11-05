"""The three daily shadow jobs: scan after the close, pretend entry in the morning, pretend exit before the close.

Only Kite quotes are used (instruments come from the local option_contracts table). Nothing here can place an order.
"""
from __future__ import annotations

from datetime import date, time, timedelta
from typing import Optional

from aitrader.backtest.bars import frame_from_bars
from aitrader.backtest.indicators import add_indicators
from aitrader.db import repo
from aitrader.jobs import NOT_LOGGED_IN, JobDeps, RunResult
from aitrader.options import atm_strike
from aitrader.shadow import rules as R
from aitrader.shadow.charges import option_round_trip_charges

SOURCE = "yfinance"
NIFTY = "NIFTY 50"
SPOT_KEY = "NSE:NIFTY 50"
MIN_DAYS_TO_EXPIRY = 7
LATE_AFTER = time(10, 30)
HOLD_DAYS = R.HOLD_DAYS


def _previous_trading_day(deps: JobDeps, day: date) -> Optional[date]:
    days = deps.calendar.trading_days(day - timedelta(days=14), day - timedelta(days=1))
    return days[-1] if days else None


def shadow_scan(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    with deps.engine.connect() as conn:
        inst = repo.instrument_by_symbol(conn, NIFTY)
        bars = repo.load_bars(conn, inst["id"], source=SOURCE, end=run_date) if inst else []
    if not bars or bars[-1].trade_date != run_date:
        return RunResult("skipped", {}, "today's NIFTY 50 bar is not loaded yet")
    df = add_indicators(frame_from_bars(bars))
    found = R.evaluate(df)
    new = 0
    with deps.engine.begin() as conn:
        missed = repo.mark_missed_shadow_signals(conn, before=run_date)
        for s in found:
            if repo.insert_shadow_signal(conn, signal_date=run_date, rule=s.rule, side=s.side, close=s.close,
                                         stop=s.stop, target=s.target, reason=s.reason, facts=s.facts):
                new += 1
    return RunResult("ok", {"signals": new, "missed": missed})


def shadow_enter(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    kite = deps.kite()
    if kite is None:
        return RunResult("skipped", {}, NOT_LOGGED_IN)
    signal_day = _previous_trading_day(deps, run_date)
    with deps.engine.connect() as conn:
        signals = repo.new_shadow_signals(conn, signal_day) if signal_day else []
        contracts = [c for _, c in repo.live_option_contracts(conn, run_date) if c.underlying == "NIFTY"]
    if not signals:
        return RunResult("ok", {"entered": 0})
    spot = float(kite.quote([SPOT_KEY])[SPOT_KEY]["last_price"])
    now = deps.now()
    late = f"late entry at {now:%H:%M}" if now.time() > LATE_AFTER else ""
    entered, failed = 0, 0
    for s in signals:
        far = [c for c in contracts if c.option_type == s["side"]
               and c.expiry >= run_date + timedelta(days=MIN_DAYS_TO_EXPIRY)]
        if not far:
            failed += 1
            continue
        expiry = min(c.expiry for c in far)
        in_expiry = [c for c in far if c.expiry == expiry]
        strike = atm_strike([c.strike for c in in_expiry], spot)
        contract = next(c for c in in_expiry if c.strike == strike)
        key = f"NFO:{contract.tradingsymbol}"
        quote = kite.quote([key]).get(key)
        if not quote:
            failed += 1
            continue
        with deps.engine.begin() as conn:
            repo.insert_shadow_trade(conn, signal_id=s["id"], rule=s["rule"], side=s["side"],
                                     tradingsymbol=contract.tradingsymbol, expiry=contract.expiry,
                                     strike=contract.strike, lot_size=contract.lot_size, entry_at=now,
                                     entry_price=float(quote["last_price"]), underlying_entry=spot,
                                     stop=float(s["stop"]), target=float(s["target"]), note=late)
        entered += 1
    return RunResult("ok" if not failed else "partial", {"entered": entered, "failed": failed})


def _exit_reason(trade: dict, spot: float, run_date: date, days_held: int) -> Optional[str]:
    stop, target = float(trade["stop"]), float(trade["target"])
    if trade["side"] == "CE":
        if spot <= stop:
            return "stop"
        if spot >= target:
            return "target"
    else:
        if spot >= stop:
            return "stop"
        if spot <= target:
            return "target"
    if trade["expiry"] - run_date <= timedelta(days=1):
        return "expiry"
    if days_held >= HOLD_DAYS:
        return "time"
    return None


def shadow_exit(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    kite = deps.kite()
    if kite is None:
        return RunResult("skipped", {}, NOT_LOGGED_IN)
    with deps.engine.connect() as conn:
        trades = repo.open_shadow_trades(conn)
    if not trades:
        return RunResult("ok", {"closed": 0, "marked": 0})
    keys = [SPOT_KEY] + [f"NFO:{t['tradingsymbol']}" for t in trades]
    quotes = kite.quote(keys)
    spot = float(quotes[SPOT_KEY]["last_price"])
    now = deps.now()
    closed = marked = 0
    for t in trades:
        q = quotes.get(f"NFO:{t['tradingsymbol']}")
        if not q:
            continue
        price = float(q["last_price"])
        entry_day = t["entry_at"].astimezone(now.tzinfo).date()
        days_held = len(deps.calendar.trading_days(entry_day + timedelta(days=1), run_date))
        reason = _exit_reason(t, spot, run_date, days_held)
        with deps.engine.begin() as conn:
            if reason:
                entry = float(t["entry_price"])
                charges = option_round_trip_charges(entry, price, t["lot_size"])
                repo.update_shadow_trade(conn, t["id"], status="closed", exit_at=now, exit_price=price,
                                         underlying_exit=spot, exit_reason=reason, charges=round(charges, 2),
                                         pnl_per_lot=round((price - entry) * t["lot_size"] - charges, 2),
                                         mark_price=price, marked_at=now, underlying_mark=spot)
                closed += 1
            else:
                repo.update_shadow_trade(conn, t["id"], mark_price=price, marked_at=now, underlying_mark=spot)
                marked += 1
    return RunResult("ok", {"closed": closed, "marked": marked})


def scoreboard(signals, trades) -> dict:
    """Per rule and per user view: closed trades, wins, win rate, total and average P&L per lot, open, missed."""
    def stats(ts, sigs):
        done = [t for t in ts if t["status"] == "closed"]
        pnl = [float(t["pnl_per_lot"]) for t in done]
        wins = sum(1 for p in pnl if p > 0)
        return {"closed": len(done), "open": sum(1 for t in ts if t["status"] == "open"), "wins": wins,
                "win_rate": wins / len(done) if done else None, "total_pnl": sum(pnl),
                "avg_pnl": sum(pnl) / len(pnl) if pnl else None,
                "missed": sum(1 for s in sigs if s["status"] == "missed"), "signals": len(sigs)}

    rules = {r: stats([t for t in trades if t["rule"] == r], [s for s in signals if s["rule"] == r])
             for r in R.RULES}
    views = {}
    for view in ("agree", "disagree", None):
        sigs = [s for s in signals if s["user_view"] == view]
        ids = {s["id"] for s in sigs}
        views[view or "no_view"] = stats([t for t in trades if t["signal_id"] in ids], sigs)
    return {"rules": rules, "views": views, "total": stats(trades, signals)}
