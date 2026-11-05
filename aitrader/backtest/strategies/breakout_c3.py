"""Strategy C3 — C2 with a 1.5-ATR stop floor, a short-term market filter (NIFTY 50 above its 50- and 200-day
averages) and a 10-day re-entry cooldown after a stop-out."""
from __future__ import annotations

from aitrader.backtest.strategies.breakout_c2 import BreakoutC2


class BreakoutC3(BreakoutC2):
    key = "breakout_c3"
    name = "Breakout, trend-confirmed"
    PARAMS = {**BreakoutC2.PARAMS, "stop_floor_atr": 1.5, "regime_filter_short": True, "reentry_cooldown_days": 10}
