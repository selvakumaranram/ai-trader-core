"""NIFTY / BANKNIFTY signal quality in R. Not option profit: no premiums, no costs."""
from __future__ import annotations

from datetime import date
from typing import Collection, List, Optional

import pandas as pd

from aitrader.backtest.rules import exit_check, gap_skip, next_trail_stop, partial_trail_check, planned_target
from aitrader.backtest.types import LONG, SHORT, IndexSignal, MoneyRules


def index_signals(frame: pd.DataFrame, symbol: str, days, strategy, money: MoneyRules,
                  blocked_entry_days: Optional[Collection[date]] = None) -> List[IndexSignal]:
    days_here = [d for d in days if d in frame.index]
    out: List[IndexSignal] = []
    for direction in (LONG, SHORT):
        out.extend(_walk(frame, symbol, days_here, strategy, direction, money, set(blocked_entry_days or ())))
    return sorted(out, key=lambda s: (s.signal_date, s.direction))


def _col(frame: pd.DataFrame, day, column: str) -> float:
    return float(frame.at[day, column]) if column in frame.columns else float("nan")


def _walk(frame, symbol, days, strategy, direction, money, blocked) -> List[IndexSignal]:
    trail = getattr(strategy, "exit_policy", "fixed_target") == "partial_trail"
    params = getattr(strategy, "PARAMS", {})
    reward = params["partial_r"] if trail else money.reward_multiple
    time_stop = params["time_stop_days"] if trail else money.time_stop_days
    out: List[IndexSignal] = []
    pending = None
    pos: Optional[dict] = None
    last = len(days) - 1
    for i, day in enumerate(days):
        o, h, low, c = (float(frame.at[day, k]) for k in ("open", "high", "low", "close"))
        d = day.date()
        if pending is not None:
            planned = planned_target(pending.signal_close, pending.stop, reward)
            ok = gap_skip(direction, o, pending.stop, planned) is None and d not in blocked
            if trail:
                ok = ok and params["min_stop"] <= abs(o - pending.stop) / o <= params["max_stop"]
            if ok:
                pos = {"cand": pending, "entry_date": d, "entry": o, "held": 0, "stop": pending.stop,
                       "partial_r": None, "target": planned_target(o, pending.stop, reward)}
            pending = None
        if pos is not None:
            entry_day = pos["entry_date"] == d
            if not entry_day:
                pos["held"] += 1
            if trail:
                hit = _trail_day(direction, pos, (o, h, low, c), entry_day, time_stop)
                if hit is None and i < last:
                    pos["stop"] = next_trail_stop(direction, pos["stop"], pos["entry"], pos["partial_r"] is not None,
                                                  _col(frame, day, "low5"), _col(frame, day, "high5"))
            else:
                hit = exit_check(direction, (o, h, low, c), pos["cand"].stop, pos["target"], entry_day=entry_day,
                                 days_held=pos["held"], time_stop=money.time_stop_days)
            if hit is None and i == last:
                hit = (c, "end_of_test")
            if hit:
                out.append(_finish(symbol, direction, pos, d, hit[0], hit[1]))
                pos = None
        if pos is None and i < last:
            row = frame.loc[[day]].copy()
            row.index = [symbol]
            found = strategy.signals(day, row, direction)
            pending = found[0] if found else None
    return out


def _r(direction: str, entry: float, stop: float, price: float) -> float:
    return (price - entry) / (entry - stop) if direction == LONG else (entry - price) / (stop - entry)


def _trail_day(direction: str, pos: dict, bar, entry_day: bool, time_stop: int):
    """D's exits for an index signal: a partial records the first half's R; returns (price, reason) on exit."""
    act = partial_trail_check(direction, bar, stop=pos["stop"], partial_level=pos["target"],
                              partial_done=pos["partial_r"] is not None, entry_day=entry_day, days_held=pos["held"],
                              time_stop=time_stop)
    if act is None:
        return None
    kind, price, reason = act
    if kind == "exit":
        return price, reason
    pos["partial_r"] = _r(direction, pos["entry"], pos["cand"].stop, price)
    again = partial_trail_check(direction, bar, stop=pos["stop"], partial_level=pos["target"], partial_done=True,
                                entry_day=True, days_held=pos["held"], time_stop=time_stop)
    return None if again is None else (again[1], again[2])


def _finish(symbol: str, direction: str, pos: dict, day: date, price: float, reason: str) -> IndexSignal:
    cand, entry = pos["cand"], pos["entry"]
    r = _r(direction, entry, cand.stop, price)
    if pos.get("partial_r") is not None:  # half closed at the partial, half here
        r = 0.5 * pos["partial_r"] + 0.5 * r
    return IndexSignal(symbol, direction, cand.signal_date, pos["entry_date"], entry, cand.stop, pos["target"], day,
                       price, reason, r, cand.reason)


def index_summary(signals: List[IndexSignal], unseen_from: date) -> List[dict]:
    out = []
    for symbol, direction in sorted({(s.symbol, s.direction) for s in signals}):
        mine = [s for s in signals if s.symbol == symbol and s.direction == direction]
        for period, chosen in (("whole", mine), ("unseen", [s for s in mine if s.entry_date >= unseen_from])):
            rs = [s.r_multiple for s in chosen]
            out.append({"symbol": symbol, "direction": direction, "period": period, "signals": len(rs),
                        "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else None,
                        "avg_r": sum(rs) / len(rs) if rs else None})
    return out
