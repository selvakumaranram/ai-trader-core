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
