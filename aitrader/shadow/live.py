"""The three daily shadow jobs: scan after the close, pretend entry in the morning, pretend exit before the close.

Only Kite quotes are used (instruments come from the local option_contracts table). Nothing here can place an order.
"""
from __future__ import annotations

from datetime import date, time, timedelta
from typing import Optional

import math

import pandas as pd

from aitrader.backtest.bars import frame_from_bars
from aitrader.backtest.costs import CostModel
from aitrader.backtest.indicators import MIN_BARS, MIN_TRADED_VALUE, add_indicators
from aitrader.backtest.rules import atr_stop_distance_check, atr_trail_stop, round_trip_estimate
from aitrader.backtest.strategies.breakout_c3 import BreakoutC3
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

# C3 stock shadow trades: a pretend ₹1,00,000 account with C3's money rules (1% risk, ₹25,000 per stock, 5 open)
STOCK_RULE = "c3_stock"
ACCOUNT, RISK_PCT, MAX_POSITION, MAX_OPEN, MAX_CHARGE_TO_RISK = 100_000.0, 0.01, 25_000.0, 5, 0.15
STOCK_TIME_STOP = BreakoutC3.PARAMS["time_stop_days"]
HISTORY_DAYS = 500  # calendar days of bars loaded for indicators (≈ 340 trading days)


def _previous_trading_day(deps: JobDeps, day: date) -> Optional[date]:
    days = deps.calendar.trading_days(day - timedelta(days=14), day - timedelta(days=1))
    return days[-1] if days else None


def _frame(conn, symbol: str, run_date: date) -> Optional[pd.DataFrame]:
    inst = repo.instrument_by_symbol(conn, symbol)
    if not inst:
        return None
    bars = repo.load_bars(conn, inst["id"], source=SOURCE, start=run_date - timedelta(days=HISTORY_DAYS),
                          end=run_date)
    if not bars or bars[-1].trade_date != run_date:
        return None
    return add_indicators(frame_from_bars(bars))


def _update_stock_trails(deps: JobDeps, run_date: date) -> int:
    """After the close: trailing stop = max(stop, highest close since entry − 3 × ATR14). It never moves down."""
    with deps.engine.connect() as conn:
        trades = [t for t in repo.open_shadow_trades(conn) if t["kind"] == "stock"]
    moved = 0
    for t in trades:
        if t["entry_at"].astimezone(deps.now().tzinfo).date() > run_date:
            continue
        with deps.engine.connect() as conn:
            df = _frame(conn, t["tradingsymbol"], run_date)
        if df is None:
            continue
        row = df.iloc[-1]
        highest = max(float(t["highest_close"] or 0), float(row["close"]))
        stop = atr_trail_stop(float(t["stop"]), highest, float(row["atr14"]), BreakoutC3.PARAMS["trail_atr"])
        with deps.engine.begin() as conn:
            repo.update_shadow_trade(conn, t["id"], highest_close=highest, stop=stop)
        moved += 1
    return moved


def _c3_signals(deps: JobDeps, run_date: date, nifty: pd.DataFrame) -> list:
    """C3's candidates on today's close (its market filter: NIFTY above both its 50- and 200-day averages)."""
    row = nifty.iloc[-1]
    if not (float(row["close"]) > float(row["sma200"]) and float(row["close"]) > float(row["sma50"])):
        return []
    frames = {}
    with deps.engine.connect() as conn:
        for inst in repo.active_instruments(conn):
            if inst["kind"] == "stock":
                df = _frame(conn, inst["symbol"], run_date)
                if df is not None:
                    frames[inst["symbol"]] = df.iloc[-1]
    if not frames:
        return []
    today = pd.DataFrame(frames).T.apply(pd.to_numeric, errors="coerce")
    eligible = today[(today["bars"] >= MIN_BARS) & (today["value20"] >= MIN_TRADED_VALUE)]
    return BreakoutC3().candidates(pd.Timestamp(run_date), eligible)


def shadow_scan(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    trailed = _update_stock_trails(deps, run_date)
    with deps.engine.connect() as conn:
        nifty = _frame(conn, NIFTY, run_date)
    if nifty is None:
        return RunResult("skipped", {"trailed": trailed}, "today's NIFTY 50 bar is not loaded yet")
    found = R.evaluate(nifty)
    stocks = _c3_signals(deps, run_date, nifty)
    new = 0
    with deps.engine.begin() as conn:
        missed = repo.mark_missed_shadow_signals(conn, before=run_date)
        for s in found:
            if repo.insert_shadow_signal(conn, signal_date=run_date, rule=s.rule, side=s.side, close=s.close,
                                         stop=s.stop, target=s.target, reason=s.reason, facts=s.facts):
                new += 1
        for c in stocks:
            if repo.insert_shadow_signal(conn, signal_date=run_date, rule=STOCK_RULE, side="BUY",
                                         close=c.signal_close, stop=c.stop, target=None, reason=c.reason,
                                         facts={**c.facts, "score": round(c.score, 4)}, underlying=c.symbol):
                new += 1
    return RunResult("ok", {"signals": new, "stock_signals": len(stocks), "missed": missed, "trailed": trailed})


def shadow_enter(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    kite = deps.kite()
    if kite is None:
        return RunResult("skipped", {}, NOT_LOGGED_IN)
    signal_day = _previous_trading_day(deps, run_date)
    with deps.engine.connect() as conn:
        all_signals = repo.new_shadow_signals(conn, signal_day) if signal_day else []
        contracts = [c for _, c in repo.live_option_contracts(conn, run_date) if c.underlying == "NIFTY"]
        open_trades = [t for t in repo.open_shadow_trades(conn) if t["kind"] == "stock"]
    open_stocks = sum(1 for t in open_trades if t["portfolio"] == "rule")
    open_picks = sum(1 for t in open_trades if t["portfolio"] == "mine")
    signals = [s for s in all_signals if s["rule"] != STOCK_RULE]
    stock_signals = sorted((s for s in all_signals if s["rule"] == STOCK_RULE),
                           key=lambda s: -(s["facts"] or {}).get("score", 0))
    if not all_signals:
        return RunResult("ok", {"entered": 0})
    now = deps.now()
    late = f"late entry at {now:%H:%M}" if now.time() > LATE_AFTER else ""
    stocks_in, stocks_skipped = _enter_stocks(deps, kite, stock_signals, open_stocks, now, late)
    agreed = [s for s in stock_signals if s["user_view"] == "agree"]
    picks_in, picks_skipped = _enter_stocks(deps, kite, agreed, open_picks, now, late, portfolio="mine")
    if not signals:
        return RunResult("ok", {"entered": stocks_in, "stocks_skipped": stocks_skipped, "picks_entered": picks_in,
                                "picks_skipped": picks_skipped})
    spot = float(kite.quote([SPOT_KEY])[SPOT_KEY]["last_price"])
    entered, failed = stocks_in, 0
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


def _enter_stocks(deps: JobDeps, kite, signals, open_now: int, now, late: str, portfolio: str = "rule"):
    """Pretend-buy C3 signals at the live price, sized like C3: 1% of a ₹1,00,000 account, ₹25,000 cap, 5 open.
    Each portfolio ("rule": every signal by score; "mine": only the signals you agreed with) has its own account
    and its own 5 slots."""
    entered = skipped = 0
    costs = CostModel()
    for s in signals:
        key = f"NSE:{s['underlying']}"
        quote = kite.quote([key]).get(key)
        reason = None
        if open_now >= MAX_OPEN:
            reason = "no free slot (5 open)"
        elif not quote:
            reason = "no live quote"
        if reason is None:
            price, stop = float(quote["last_price"]), float(s["stop"])
            cand = type("C", (), {"stop": stop, "facts": s["facts"]})()
            if atr_stop_distance_check(cand, price, BreakoutC3.PARAMS["entry_min_atr"],
                                       BreakoutC3.PARAMS["entry_max_atr"]):
                reason = "stop distance out of range at entry (1–3 ATR)"
            else:
                shares = min(math.floor(ACCOUNT * RISK_PCT / (price - stop)), math.floor(MAX_POSITION / price))
                if shares < 1:
                    reason = "one share is more than the per-stock cap"
                elif round_trip_estimate(costs, shares, price, price) > MAX_CHARGE_TO_RISK * shares * (price - stop):
                    reason = "charges too high vs risk"
        with deps.engine.begin() as conn:
            if reason:
                if portfolio == "rule":
                    repo.set_shadow_signal_status(conn, s["id"], "skipped", reason)
                else:
                    repo.set_shadow_pick_note(conn, s["id"], f"skipped: {reason}")
                skipped += 1
                continue
            repo.insert_shadow_trade(conn, signal_id=s["id"], rule=STOCK_RULE, side="BUY", kind="stock",
                                     portfolio=portfolio,
                                     tradingsymbol=s["underlying"], lot_size=shares, entry_at=now,
                                     entry_price=price, underlying_entry=price, stop=stop, initial_stop=stop,
                                     highest_close=price, target=0, note=late)
        entered += 1
        open_now += 1
    return entered, skipped


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
    def key(t):
        return f"NSE:{t['tradingsymbol']}" if t["kind"] == "stock" else f"NFO:{t['tradingsymbol']}"

    quotes = kite.quote([SPOT_KEY] + [key(t) for t in trades])
    spot = float(quotes[SPOT_KEY]["last_price"])
    now = deps.now()
    closed = marked = 0
    costs = CostModel()
    for t in trades:
        q = quotes.get(key(t))
        if not q:
            continue
        price = float(q["last_price"])
        entry_day = t["entry_at"].astimezone(now.tzinfo).date()
        days_held = len(deps.calendar.trading_days(entry_day + timedelta(days=1), run_date))
        if t["kind"] == "stock":
            closed += _exit_stock(deps, t, price, days_held, now, costs)
            continue
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


def _exit_stock(deps: JobDeps, t: dict, price: float, days_held: int, now, costs: CostModel) -> int:
    """At 15:25: sell if the live price is at or below the (trailing) stop, or after C3's 40-day time stop."""
    stop, initial = float(t["stop"]), float(t["initial_stop"])
    reason = None
    if price <= stop:
        reason = "trail_stop" if stop > initial else "stop"
    elif days_held >= STOCK_TIME_STOP:
        reason = "time"
    with deps.engine.begin() as conn:
        if not reason:
            repo.update_shadow_trade(conn, t["id"], mark_price=price, marked_at=now, underlying_mark=price)
            return 0
        shares, entry = t["lot_size"], float(t["entry_price"])
        charges = costs.buy_charges(shares * entry) + costs.sell_charges(shares * price)
        pnl = (price - entry) * shares - charges
        repo.update_shadow_trade(conn, t["id"], status="closed", exit_at=now, exit_price=price,
                                 underlying_exit=price, exit_reason=reason, charges=round(charges, 2),
                                 pnl_per_lot=round(pnl, 2), mark_price=price, marked_at=now, underlying_mark=price,
                                 r_multiple=round(pnl / (shares * (entry - initial)), 4))
    return 1


def scoreboard(signals, trades) -> dict:
    """Per rule, per user view and per stock portfolio: closed trades, wins, win rate, total and average P&L per lot, open, missed."""
    def stats(ts, sigs):
        done = [t for t in ts if t["status"] == "closed"]
        pnl = [float(t["pnl_per_lot"]) for t in done]
        rs = [float(t["r_multiple"]) for t in done if t.get("r_multiple") is not None]
        wins = sum(1 for p in pnl if p > 0)
        return {"closed": len(done), "open": sum(1 for t in ts if t["status"] == "open"), "wins": wins,
                "win_rate": wins / len(done) if done else None, "total_pnl": sum(pnl),
                "avg_pnl": sum(pnl) / len(pnl) if pnl else None, "avg_r": sum(rs) / len(rs) if rs else None,
                "missed": sum(1 for s in sigs if s["status"] == "missed"), "signals": len(sigs)}

    picks = [t for t in trades if t.get("portfolio", "rule") == "mine"]
    trades = [t for t in trades if t.get("portfolio", "rule") == "rule"]  # rule and view counts: the rule's trades
    stock_sigs = [s for s in signals if s["rule"] == STOCK_RULE]
    portfolios = {"rule": stats([t for t in trades if t["rule"] == STOCK_RULE], stock_sigs),
                  "mine": stats(picks, [s for s in stock_sigs if s["user_view"] == "agree"])}

    rules = {r: stats([t for t in trades if t["rule"] == r], [s for s in signals if s["rule"] == r])
             for r in [*R.RULES, STOCK_RULE]}
    views = {}
    for view in ("agree", "disagree", None):
        sigs = [s for s in signals if s["user_view"] == view]
        ids = {s["id"] for s in sigs}
        views[view or "no_view"] = stats([t for t in trades if t["signal_id"] in ids], sigs)
    return {"rules": rules, "views": views, "total": stats(trades, signals), "portfolios": portfolios}
