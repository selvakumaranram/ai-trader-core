"""Strategy B2 — trend pullback with a volatility-based stop, checked against the actual entry price."""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from aitrader.backtest.types import LONG, Candidate


class PullbackB2:
    key = "pullback_b2"
    name = "Trend pullback, volatility-based stop"
    PARAMS = {"near_sma20": 0.01, "stop": "min(lowest low of last 3 bars, close − 1.5 × ATR14)", "stop_atr": 1.5,
              "entry_min_atr": 1.0, "entry_max_atr": 3.0, "rs_days": 126}

    def __init__(self):
        self.nifty_ret126: Dict[date, float] = {}  # set by the runner: NIFTY 50's 126-day return by date
        self.skips: Counter = Counter()

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["ret126"] = out["close"] / out["close"].shift(self.PARAMS["rs_days"]) - 1
        return out

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        if rows.empty:
            return []
        c, o, s20, s50, s200 = rows["close"], rows["open"], rows["sma20"], rows["sma50"], rows["sma200"]
        mask = (c > s50) & (s50 > s200) & (rows["low"] <= s20 * (1 + self.PARAMS["near_sma20"])) & (c > s20) \
            & (c > o) & rows["atr14"].notna()
        nifty = self.nifty_ret126.get(day.date(), float("nan"))
        out = []
        for symbol in sorted(rows.index[mask]):
            r = rows.loc[symbol]
            if not float(r["ret126"]) > nifty:  # NaN on either side fails too
                self.skips["weaker_than_nifty"] += 1
                continue
            atr_stop = float(r["close"]) - self.PARAMS["stop_atr"] * float(r["atr14"])
            stop = min(float(r["low3"]), atr_stop)
            reason = (f"Uptrend: close {r['close']:.2f}, 50-day avg {r['sma50']:.2f}, 200-day avg {r['sma200']:.2f}; "
                      f"price dipped to the 20-day avg {r['sma20']:.2f} and turned. 6-month return "
                      f"{r['ret126']:+.1%} beats NIFTY 50's {nifty:+.1%}. Stop at {stop:.2f} (the lower of the "
                      f"3-day low and 1.5×ATR below the close).")
            facts = {k: round(float(r[k]), 4) for k in ("close", "sma20", "sma50", "sma200", "atr14", "ret126")}
            facts["nifty_ret126"] = round(nifty, 4)
            out.append(Candidate(str(symbol), LONG, day.date(), float(r["close"]), stop,
                                 float((r["sma50"] - r["sma200"]) / r["sma200"]), reason, facts))
        return out

    def entry_check(self, cand: Candidate, fill: float) -> Optional[str]:
        """Once the actual entry price is known: the stop must be 1–3 ATR below it."""
        atr = cand.facts["atr14"]
        dist = fill - cand.stop
        if not self.PARAMS["entry_min_atr"] * atr <= dist <= self.PARAMS["entry_max_atr"] * atr:
            return "stop_distance_at_entry"
        return None
