"""Strategy A2 — medium-term momentum: weekly ranking on 6- and 12-month returns that skip the latest month."""
from __future__ import annotations

from typing import Dict, List, Set

import numpy as np
import pandas as pd

from aitrader.backtest.indicators import sma
from aitrader.backtest.types import LONG, Candidate

WEIGHTS = {"r6": 50, "r12": 30, "r6_voladj": 20}


class MomentumA2:
    key = "momentum_a2"
    name = "Medium-term momentum"
    exit_policy = "atr_trail"
    signal_frequency = "weekly"
    PARAMS = {"weights": WEIGHTS, "skip_days": 21, "r6_days": 126, "r12_days": 252, "vol_days": 126,
              "min_bars": 260, "min_value50": 1e8, "top_n": 10, "keep_top": 30, "stop_atr": 2.5,
              "trail_atr": 3.0, "time_stop_days": 60, "signal_weekday": None}
    # keep_top None = no rank exit (only a close below the 200-day average exits on the weekly day);
    # time_stop_days None = no time stop; signal_weekday None = last trading day of the week (2 = Wednesday)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.PARAMS
        out = df.copy()
        close = out["close"]
        skipped = close.shift(p["skip_days"])
        out["r6"] = skipped / close.shift(p["r6_days"]) - 1
        out["r12"] = skipped / close.shift(p["r12_days"]) - 1
        std = close.pct_change().rolling(p["vol_days"], min_periods=p["vol_days"]).std()
        out["r6_voladj"] = out["r6"] / std.where(std > 0)
        out["value50"] = sma(close * out["volume"].astype(float), 50)
        return out

    def _ranked(self, rows: pd.DataFrame) -> pd.DataFrame:
        p = self.PARAMS
        cols = ["close", "sma50", "sma200", "atr14", "bars", "value50", *WEIGHTS]
        df = rows[cols].replace([np.inf, -np.inf], np.nan).dropna()
        df = df[(df["bars"] >= p["min_bars"]) & (df["close"] > df["sma200"]) & (df["close"] > df["sma50"])
                & (df["value50"] >= p["min_value50"])].sort_index()
        if df.empty:
            return df.assign(score=pd.Series(dtype=float))
        score = sum(w * df[k].rank(pct=True) for k, w in WEIGHTS.items())  # percentile 0–100 × weight / 100
        return df.assign(score=score).sort_values("score", ascending=False, kind="mergesort")

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        if rows.empty:
            return []
        df = self._ranked(rows)
        out = []
        for pos, (symbol, r) in enumerate(df.head(self.PARAMS["top_n"]).iterrows(), start=1):
            stop = r["close"] - self.PARAMS["stop_atr"] * r["atr14"]
            if not 0 < stop < r["close"]:
                continue
            reason = (f"Ranked #{pos} of {len(df)} on medium-term momentum (6-month {r['r6']:+.1%} and 12-month "
                      f"{r['r12']:+.1%}, both ending a month ago); above its 50- and 200-day averages. "
                      f"Stop 2.5×ATR below at {stop:.2f}, then trailing 3×ATR under the highest close.")
            facts = {k: round(float(r[k]), 4) for k in (*WEIGHTS, "score", "atr14")}
            facts["rank"] = pos
            out.append(Candidate(str(symbol), LONG, day.date(), float(r["close"]), float(stop), float(r["score"]),
                                 reason, facts))
        return out

    def weekly_exits(self, day: pd.Timestamp, eligible: pd.DataFrame, today: pd.DataFrame,
                     held: Set[str]) -> Dict[str, str]:
        """Held stocks to sell at the next open, with the reason."""
        if self.PARAMS.get("keep_top"):
            keep = self.rank_keep(day, eligible)
            return {s: "rank_exit" for s in held if s not in keep}
        out = {}
        for s in held:
            if s in today.index and float(today.at[s, "close"]) <= float(today.at[s, "sma200"]):
                out[s] = "below_sma200"
        return out

    def rank_keep(self, day: pd.Timestamp, rows: pd.DataFrame) -> Set[str]:
        """Held stocks to keep at a weekly check: still eligible (so above the 200-day average) and in the top 30."""
        if rows.empty:
            return set()
        return {str(s) for s in self._ranked(rows).head(self.PARAMS["keep_top"]).index}
