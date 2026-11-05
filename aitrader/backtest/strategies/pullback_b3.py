"""Strategy B3 — B2's pullback entry, but winners run: no target, 3-ATR trailing stop, 40-day time stop, and a
10-day re-entry cooldown after a stop-out."""
from __future__ import annotations

from aitrader.backtest.strategies.pullback_b2 import PullbackB2


class PullbackB3(PullbackB2):
    key = "pullback_b3"
    name = "Trend pullback, let winners run"
    exit_policy = "atr_trail"
    PARAMS = {**PullbackB2.PARAMS, "trail_atr": 3.0, "time_stop_days": 40, "reentry_cooldown_days": 10}
