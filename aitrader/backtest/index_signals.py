"""NIFTY / BANKNIFTY signal quality in R. Not option profit: no premiums, no costs."""
from __future__ import annotations

from datetime import date
from typing import List, Optional

import pandas as pd

from aitrader.backtest.rules import exit_check, gap_skip, planned_target
from aitrader.backtest.types import LONG, SHORT, IndexSignal, MoneyRules


def index_signals(frame: pd.DataFrame, symbol: str, days, strategy, money: MoneyRules) -> List[IndexSignal]:
    days_here = [d for d in days if d in frame.index]
    out: List[IndexSignal] = []
    for direction in (LONG, SHORT):
        out.extend(_walk(frame, symbol, days_here, strategy, direction, money))
    return sorted(out, key=lambda s: (s.signal_date, s.direction))


def _walk(frame, symbol, days, strategy, direction, money) -> List[IndexSignal]:
    out: List[IndexSignal] = []
    pending = None
    pos: Optional[dict] = None
    last = len(days) - 1
    for i, day in enumerate(days):
        o, h, low, c = (float(frame.at[day, k]) for k in ("open", "high", "low", "close"))
        d = day.date()
        if pending is not None:
            planned = planned_target(pending.signal_close, pending.stop, money.reward_multiple)
            if gap_skip(direction, o, pending.stop, planned) is None:
                pos = {"cand": pending, "entry_date": d, "entry": o, "held": 0,
                       "target": planned_target(o, pending.stop, money.reward_multiple)}
            pending = None
        if pos is not None:
            entry_day = pos["entry_date"] == d
            if not entry_day:
                pos["held"] += 1
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


def _finish(symbol: str, direction: str, pos: dict, day: date, price: float, reason: str) -> IndexSignal:
    cand, entry = pos["cand"], pos["entry"]
    if direction == LONG:
        r = (price - entry) / (entry - cand.stop)
    else:
        r = (entry - price) / (cand.stop - entry)
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
