from __future__ import annotations

import math
from typing import Optional, Tuple

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


def size_position(fill: float, stop: float, money: MoneyRules) -> int:
    risk_per_share = fill - stop
    if risk_per_share <= 0:
        return 0
    return max(0, min(math.floor(money.risk_per_trade / risk_per_share),
                      math.floor(money.max_position_value / fill)))


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
