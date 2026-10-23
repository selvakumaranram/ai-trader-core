from __future__ import annotations

from typing import List

import pandas as pd

from aitrader.backtest.types import LONG, Candidate


class Pullback:
    key = "pullback"
    name = "Trend pullback"
    PARAMS = {"near_sma20": 0.01, "min_stop": 0.01, "max_stop": 0.08, "stop": "lowest low of last 3 bars"}

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        return self.signals(day, rows, LONG)

    def signals(self, day: pd.Timestamp, rows: pd.DataFrame, direction: str) -> List[Candidate]:
        if rows.empty:
            return []
        c, o, s20, s50, s200 = rows["close"], rows["open"], rows["sma20"], rows["sma50"], rows["sma200"]
        if direction == LONG:
            mask = (c > s50) & (s50 > s200) & (rows["low"] <= s20 * 1.01) & (c > s20) & (c > o)
            stop, dist, score = rows["low3"], (c - rows["low3"]) / c, (s50 - s200) / s200
        else:
            mask = (c < s50) & (s50 < s200) & (rows["high"] >= s20 * 0.99) & (c < s20) & (c < o)
            stop, dist, score = rows["high3"], (rows["high3"] - c) / c, (s200 - s50) / s200
        mask &= (dist >= self.PARAMS["min_stop"]) & (dist <= self.PARAMS["max_stop"])
        out = []
        for symbol in sorted(rows.index[mask]):
            r = rows.loc[symbol]
            trend = "Uptrend" if direction == LONG else "Downtrend"
            side = "dipped to" if direction == LONG else "rallied to"
            reason = (f"{trend}: close {r['close']:.2f}, 50-day avg {r['sma50']:.2f}, 200-day avg {r['sma200']:.2f}; "
                      f"price {side} the 20-day avg {r['sma20']:.2f} and turned. Stop at {stop[symbol]:.2f} "
                      f"(3-day {'low' if direction == LONG else 'high'}).")
            facts = {k: round(float(r[k]), 4) for k in ("close", "sma20", "sma50", "sma200")}
            out.append(Candidate(str(symbol), direction, day.date(), float(r["close"]), float(stop[symbol]),
                                 float(score[symbol]), reason, facts))
        return out
