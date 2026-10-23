from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from aitrader.backtest.types import LONG, Candidate


class Breakout:
    key = "breakout"
    name = "Breakout"
    PARAMS = {"lookback": 20, "volume_surge": 1.5, "atr_mult": 1.0, "max_stop": 0.08}

    def __init__(self, use_volume: bool = True):
        self.use_volume = use_volume

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        return self.signals(day, rows, LONG)

    def signals(self, day: pd.Timestamp, rows: pd.DataFrame, direction: str) -> List[Candidate]:
        if rows.empty:
            return []
        c, atr = rows["close"], rows["atr14"]
        with np.errstate(divide="ignore", invalid="ignore"):
            surge = rows["volume"] / rows["vol20_prior"].where(rows["vol20_prior"] > 0)
        if direction == LONG:
            level = rows["high20_prior"]
            stop = level - self.PARAMS["atr_mult"] * atr
            mask = (c > level) & (c > rows["sma50"]) & (stop < c) & ((c - stop) / c <= self.PARAMS["max_stop"])
        else:
            level = rows["low20_prior"]
            stop = level + self.PARAMS["atr_mult"] * atr
            mask = (c < level) & (c < rows["sma50"]) & (stop > c) & ((stop - c) / c <= self.PARAMS["max_stop"])
        if self.use_volume:
            mask &= surge >= self.PARAMS["volume_surge"]
            score = surge
        else:
            score = (c - level).abs() / atr
        out = []
        for symbol in sorted(rows.index[mask]):
            r = rows.loc[symbol]
            word = "above its 20-day high" if direction == LONG else "below its 20-day low"
            vol = f" on {surge[symbol]:.1f}× normal volume" if self.use_volume else ""
            reason = (f"Closed at {r['close']:.2f}, {word} {level[symbol]:.2f}{vol}. "
                      f"Stop {stop[symbol]:.2f} (1×ATR beyond the breakout level).")
            facts = {"close": round(float(r["close"]), 4), "level": round(float(level[symbol]), 4),
                     "atr14": round(float(atr[symbol]), 4)}
            if self.use_volume:
                facts["volume_surge"] = round(float(surge[symbol]), 4)
            out.append(Candidate(str(symbol), direction, day.date(), float(r["close"]), float(stop[symbol]),
                                 float(score[symbol]), reason, facts))
        return out
