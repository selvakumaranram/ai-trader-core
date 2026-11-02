"""Strategy C2 — C's breakout entry with the stop distance checked at the actual entry and a trailing exit."""
from __future__ import annotations

from typing import Optional

from aitrader.backtest.rules import atr_stop_distance_check
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.types import Candidate


class BreakoutC2(Breakout):
    key = "breakout_c2"
    name = "Breakout, entry-checked, trailing exit"
    exit_policy = "atr_trail"
    PARAMS = {**Breakout.PARAMS, "max_stop": None, "entry_min_atr": 1.0, "entry_max_atr": 3.0, "trail_atr": 3.0,
              "time_stop_days": 40}

    def entry_check(self, cand: Candidate, fill: float) -> Optional[str]:
        """Entry − stop must be 1–3 × ATR(14) at the actual entry price (this also blocks big gap-ups)."""
        return atr_stop_distance_check(cand, fill, self.PARAMS["entry_min_atr"], self.PARAMS["entry_max_atr"])
