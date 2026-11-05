"""Strategy C4 — breakout + momentum: C3's breakout signal, taken only when the stock is also in the top 30 of A2's
medium-term momentum ranking that day. C3's stop, market filter, cooldown and trailing exit are unchanged."""
from __future__ import annotations

from typing import List

import pandas as pd

from aitrader.backtest.strategies.breakout_c3 import BreakoutC3
from aitrader.backtest.strategies.momentum_a2 import MomentumA2
from aitrader.backtest.types import Candidate


class BreakoutMomentum(BreakoutC3):
    key = "breakout_momentum"
    name = "Breakout + momentum"
    PARAMS = {**BreakoutC3.PARAMS, "momentum_top": 30}

    def __init__(self, use_volume: bool = True):
        super().__init__(use_volume)
        self._momentum = MomentumA2()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._momentum.prepare(df)

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        breakouts = super().candidates(day, rows)
        if not breakouts:
            return []
        ranked = self._momentum._ranked(rows)
        rank = {str(s): i for i, s in enumerate(ranked.index[:self.PARAMS["momentum_top"]], start=1)}
        out = []
        for c in breakouts:
            if c.symbol in rank:
                out.append(Candidate(c.symbol, c.direction, c.signal_date, c.signal_close, c.stop, c.score,
                                     f"{c.reason} Momentum rank #{rank[c.symbol]} of {len(ranked)}.",
                                     {**c.facts, "momentum_rank": rank[c.symbol]}))
        return out
