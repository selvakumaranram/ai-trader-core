from __future__ import annotations

import math
from datetime import date
from typing import List, Optional, Sequence, Set, Tuple

from aitrader.backtest.types import LONG, MoneyRules

Bar4 = Tuple[float, float, float, float]


def planned_target(ref: float, stop: float, multiple: float) -> float:
    return ref + multiple * (ref - stop)


def gap_skip(direction: str, open_: float, stop: float, target: float) -> Optional[str]:
    if direction == LONG:
        if open_ <= stop:
            return "gap_through_stop"
        if open_ >= target:
            return "gap_past_target"
    else:
        if open_ >= stop:
            return "gap_through_stop"
        if open_ <= target:
            return "gap_past_target"
    return None


def exit_check(direction: str, bar: Bar4, stop: float, target: float, *, entry_day: bool, days_held: int,
               time_stop: int) -> Optional[Tuple[float, str]]:
    o, h, low, c = bar
    if direction == LONG:
        if not entry_day:
            if o <= stop:
                return o, "stop_gap"
            if o >= target:
                return o, "target_gap"
        hit_stop, hit_target = low <= stop, h >= target
    else:
        if not entry_day:
            if o >= stop:
                return o, "stop_gap"
            if o <= target:
                return o, "target_gap"
        hit_stop, hit_target = h >= stop, low <= target
    if hit_stop and hit_target:
        return stop, "stop_same_day"  # daily bars cannot tell which came first: assume the loss
    if hit_stop:
        return stop, "stop"
    if hit_target:
        return target, "target"
    if days_held >= time_stop:
        return c, "time"
    return None


def size_position(fill: float, stop: float, money: MoneyRules, risk: Optional[float] = None) -> int:
    risk_per_share = fill - stop
    if risk_per_share <= 0:
        return 0
    return max(0, min(risk_shares(fill, stop, money.risk_per_trade if risk is None else risk),
                      math.floor(money.max_position_value / fill)))


def risk_shares(fill: float, stop: float, risk: float) -> int:
    """Shares the risk amount alone would buy, before the position cap and cash."""
    return max(0, math.floor(risk / (fill - stop))) if fill > stop else 0


def round_trip_estimate(costs, shares: int, fill: float, open_: float) -> float:
    """Charges + slippage for buying `shares` at `fill` and selling them back at today's open (an estimate)."""
    sell_value = shares * open_ * (1 - costs.slippage)
    return (costs.buy_charges(shares * fill) + costs.sell_charges(sell_value)
            + shares * open_ * costs.slippage * 2)


def atr_stop_distance_check(cand, fill: float, min_atr: float, max_atr: float) -> Optional[str]:
    """Once the actual entry price is known: entry − stop must be between min_atr and max_atr × the signal-day ATR."""
    atr = cand.facts["atr14"]
    if not min_atr * atr <= fill - cand.stop <= max_atr * atr:
        return "stop_distance_at_entry"
    return None


def atr_trail_stop(stop: float, highest_close: float, atr: float, multiple: float = 3.0) -> float:
    """Strategy A2's trail: highest close since entry − multiple × ATR, never lower than the current stop."""
    candidate = highest_close - multiple * atr
    return stop if math.isnan(candidate) else max(stop, candidate)


def week_end_days(days: Sequence) -> Set:
    """The last trading day of each ISO week in `days` (a holiday Friday makes Thursday the week's end)."""
    out = set()
    for i, day in enumerate(days):
        if i == len(days) - 1 or days[i + 1].isocalendar()[:2] != day.isocalendar()[:2]:
            out.add(day)
    return out


def week_signal_days(days: Sequence, weekday: Optional[int] = None) -> Set:
    """Weekly signal days: the last trading day of each week (weekday None), or the given weekday (0 = Monday) —
    in a holiday week the last trading day before it; a week with no trading day up to it has no signal day."""
    if weekday is None:
        return week_end_days(days)
    best: dict = {}
    for day in days:
        if day.weekday() <= weekday:
            key = day.isocalendar()[:2]
            if key not in best or day > best[key]:
                best[key] = day
    return set(best.values())


class RiskScaler:
    """Half the risk while equity is more than `cut` below its all-time peak (which never resets); full risk again
    once equity is back within `restore` of that peak."""

    def __init__(self, cut: float = 0.10, restore: float = 0.05):
        self.cut, self.restore = cut, restore
        self.peak: Optional[float] = None
        self.factor = 1.0

    def update(self, equity: float) -> None:
        self.peak = equity if self.peak is None else max(self.peak, equity)
        if self.factor == 1.0 and equity < self.peak * (1 - self.cut):
            self.factor = 0.5
        elif self.factor < 1.0 and equity >= self.peak * (1 - self.restore):
            self.factor = 1.0


class DrawdownGuard:
    """Pause new entries when equity falls `limit` below its peak; resume once at least `min_days` trading days
    have passed since the pause began and NIFTY 50 is above its 200-day average. The peak resets on resume."""

    def __init__(self, limit: float, min_days: int, start_equity: float):
        self.limit, self.min_days, self.peak = limit, min_days, start_equity
        self.paused = False
        self._since = 0
        self.log: List[dict] = []

    def update(self, i: int, day: date, equity: float, regime_on: bool) -> None:
        if self.paused:
            if i - self._since >= self.min_days and regime_on:
                self.paused, self.peak = False, equity
                self.log[-1].update(resume_date=day.isoformat(), equity_at_resume=equity, days_paused=i - self._since)
            return
        self.peak = max(self.peak, equity)
        if equity <= self.peak * (1 - self.limit):
            self.paused, self._since = True, i
            self.log.append({"start_date": day.isoformat(), "resume_date": None, "equity_at_pause": equity,
                             "equity_at_resume": None, "days_paused": None})

    def finish(self, last_index: int) -> List[dict]:
        if self.paused:
            self.log[-1]["days_paused"] = last_index - self._since
        return self.log


def partial_trail_check(direction: str, bar: Bar4, *, stop: float, partial_level: float, partial_done: bool,
                        entry_day: bool, days_held: int, time_stop: int) -> Optional[Tuple[str, float, str]]:
    """Strategy D's exits: ("partial", price, reason) sells half; ("exit", price, reason) closes the rest.

    Daily bars cannot order events inside a day, so a bar touching both the stop and the partial level counts
    the stop first.
    """
    o, h, low, c = bar
    long = direction == LONG
    if not entry_day:
        if (o <= stop) if long else (o >= stop):
            return "exit", o, "stop_gap"
        if not partial_done and ((o >= partial_level) if long else (o <= partial_level)):
            return "partial", o, "partial_gap"
    hit_stop = (low <= stop) if long else (h >= stop)
    hit_partial = not partial_done and ((h >= partial_level) if long else (low <= partial_level))
    if hit_stop and hit_partial:
        return "exit", stop, "stop_same_day"
    if hit_stop:
        return "exit", stop, "trail_stop" if partial_done else "stop"
    if hit_partial:
        return "partial", partial_level, "partial"
    if days_held >= time_stop:
        return "exit", c, "time"
    return None


def next_trail_stop(direction: str, stop: float, entry: float, partial_done: bool, low5: float,
                    high5: float) -> float:
    """After the partial: breakeven, then trail behind the last 5 bars' extreme. Effective from the next day."""
    if not partial_done:
        return stop
    if direction == LONG:
        return max(v for v in (stop, entry, low5) if not math.isnan(v))
    return min(v for v in (stop, entry, high5) if not math.isnan(v))
