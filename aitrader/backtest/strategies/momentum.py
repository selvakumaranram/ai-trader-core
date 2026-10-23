from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from aitrader.backtest.types import LONG, Candidate

WEIGHTS = {"ret21": 30, "ret5": 25, "ret3": 15, "ret1": 10, "vol_surge": 10}
RSI_WEIGHT, TOTAL_WEIGHT = 5, 95  # the doc's delivery-% weight (5) has no data source yet


class Momentum:
    key = "momentum"
    name = "Momentum"
    PARAMS = {"weights": WEIGHTS, "rsi_weight": RSI_WEIGHT, "rsi_band": [55, 70], "top_n": 10, "atr_mult": 2.0}

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        if rows.empty:
            return []
        with np.errstate(divide="ignore", invalid="ignore"):
            surge = rows["vol5"] / rows["vol50"].where(rows["vol50"] > 0)
        df = rows[["ret21", "ret5", "ret3", "ret1", "rsi14", "atr14", "sma50", "close"]].assign(vol_surge=surge)
        df = df.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
        if df.empty:
            return []
        score = sum(w * df[k].rank(pct=True) * 100 for k, w in WEIGHTS.items())
        lo, hi = self.PARAMS["rsi_band"]
        score = score + RSI_WEIGHT * df["rsi14"].between(lo, hi).astype(float) * 100
        df = df.assign(score=score / TOTAL_WEIGHT).sort_values("score", ascending=False, kind="mergesort")
        out = []
        for pos, (symbol, r) in enumerate(df.head(self.PARAMS["top_n"]).iterrows(), start=1):
            stop = r["close"] - self.PARAMS["atr_mult"] * r["atr14"]
            if not (r["close"] > r["sma50"]) or not (0 < stop < r["close"]):
                continue
            reason = (f"Ranked #{pos} of {len(df)} on momentum (21-day {r['ret21']:+.1%}, 5-day {r['ret5']:+.1%}, "
                      f"volume {r['vol_surge']:.1f}× normal, RSI {r['rsi14']:.0f}); above its 50-day average. "
                      f"Stop 2×ATR below at {stop:.2f}.")
            facts = {k: round(float(r[k]), 4) for k in ("ret21", "ret5", "ret3", "ret1", "vol_surge", "rsi14", "score")}
            facts["rank"] = pos
            out.append(Candidate(str(symbol), LONG, day.date(), float(r["close"]), float(stop), float(r["score"]),
                                 reason, facts))
        return out
