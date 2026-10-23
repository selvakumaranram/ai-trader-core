"""Day-by-day portfolio simulation. Decisions at day t use rows <= t; entries fill at t+1's open."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Sequence, Tuple

import pandas as pd

from aitrader.backtest.costs import CostModel
from aitrader.backtest.indicators import MIN_BARS, MIN_TRADED_VALUE
from aitrader.backtest.rules import exit_check, gap_skip, planned_target, size_position
from aitrader.backtest.types import LONG, Candidate, EquityPoint, MoneyRules, Trade

_EMPTY = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "bars", "value20"])


@dataclass
class SimResult:
    trades: List[Trade]
    equity: List[EquityPoint]
    skips: Dict[str, int]
    notes: Dict[str, int]


@dataclass
class _Position:
    cand: Candidate
    entry_date: date
    entry_price: float
    shares: int
    target: float
    entry_charges: float
    last_close: float
    days_held: int = 0


def day_slices(frames: Dict[str, pd.DataFrame], days: Sequence[pd.Timestamp]) -> Dict[pd.Timestamp, pd.DataFrame]:
    """One frame per day, indexed by symbol, holding every symbol that has a bar that day."""
    if not frames or not days:
        return {}
    panel = pd.concat(frames, names=["symbol", "date"])
    dates = panel.index.get_level_values("date")
    panel = panel[(dates >= days[0]) & (dates <= days[-1])]
    return {day: g.droplevel("date") for day, g in panel.groupby(level="date")}


def _close(pos: _Position, day: date, price: float, reason: str, costs: CostModel) -> Tuple[Trade, float]:
    fill = price * (1 - costs.slippage)
    value = pos.shares * fill
    sell = costs.sell_charges(value)
    pnl = value - sell - (pos.shares * pos.entry_price + pos.entry_charges)
    risk = pos.shares * (pos.entry_price - pos.cand.stop)
    trade = Trade(pos.cand.symbol, pos.cand.signal_date, pos.entry_date, pos.entry_price, pos.shares, pos.cand.stop,
                  pos.target, day, fill, reason, pos.entry_charges + sell, pnl, pnl / risk, pos.cand.reason,
                  dict(pos.cand.facts))
    return trade, value - sell


def simulate(frames: Dict[str, pd.DataFrame], days: Sequence[pd.Timestamp], strategy, money: MoneyRules,
             costs: CostModel) -> SimResult:
    by_day = day_slices(frames, days)
    cash = money.capital
    positions: Dict[str, _Position] = {}
    pending: List[Candidate] = []
    pause = False
    trades: List[Trade] = []
    equity: List[EquityPoint] = []
    skips: Counter = Counter()
    notes: Counter = Counter()
    last = len(days) - 1

    for i, day in enumerate(days):
        today = by_day.get(day, _EMPTY)
        d = day.date()

        # 1. entries at the open, decided at yesterday's close
        for cand in sorted(pending, key=lambda c: (-c.score, c.symbol)):
            if pause:
                skips["daily_loss_pause"] += 1
                continue
            if cand.symbol in positions:
                skips["already_held"] += 1
                continue
            if len(positions) >= money.max_positions:
                skips["no_slot"] += 1
                continue
            if cand.symbol not in today.index:
                skips["missing_bar"] += 1
                continue
            open_ = float(today.at[cand.symbol, "open"])
            gap = gap_skip(LONG, open_, cand.stop,
                           planned_target(cand.signal_close, cand.stop, money.reward_multiple))
            if gap:
                skips[gap] += 1
                continue
            fill = open_ * (1 + costs.slippage)
            shares = size_position(fill, cand.stop, money)
            if shares < 1:
                skips["size_zero"] += 1
                continue
            while shares > 0 and shares * fill + costs.buy_charges(shares * fill) > cash:
                shares -= 1
            if shares < 1:
                skips["no_cash"] += 1
                continue
            charges = costs.buy_charges(shares * fill)
            cash -= shares * fill + charges
            positions[cand.symbol] = _Position(cand, d, fill, shares,
                                               planned_target(fill, cand.stop, money.reward_multiple), charges, fill)
        pending = []

        # 2. exits
        realised = 0.0
        for symbol in list(positions):
            pos = positions[symbol]
            if symbol not in today.index:
                notes["missing_bar_days"] += 1
                continue
            bar = tuple(float(today.at[symbol, k]) for k in ("open", "high", "low", "close"))
            entry_day = pos.entry_date == d
            if not entry_day:
                pos.days_held += 1
            pos.last_close = bar[3]
            hit = exit_check(LONG, bar, pos.cand.stop, pos.target, entry_day=entry_day, days_held=pos.days_held,
                             time_stop=money.time_stop_days)
            if hit is None and i == last:
                hit = (bar[3], "end_of_test")
            if hit:
                trade, proceeds = _close(pos, d, hit[0], hit[1], costs)
                cash += proceeds
                realised += trade.pnl
                trades.append(trade)
                del positions[symbol]
        if i == last:  # positions with no bar on the last day close at their last known close
            for symbol in list(positions):
                pos = positions.pop(symbol)
                trade, proceeds = _close(pos, d, pos.last_close, "end_of_test", costs)
                cash += proceeds
                realised += trade.pnl
                trades.append(trade)

        # 3. daily loss pause, 4. equity
        pause = realised <= -money.daily_loss_limit
        invested = sum(p.shares * p.last_close for p in positions.values())
        equity.append(EquityPoint(d, cash, invested, cash + invested))

        # 5. candidates at the close
        if i < last:
            eligible = today[(today["bars"] >= MIN_BARS) & (today["value20"] >= MIN_TRADED_VALUE)]
            pending = strategy.candidates(day, eligible)

    return SimResult(trades, equity, dict(skips), dict(notes))
