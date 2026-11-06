"""Strategy M — Malaysian support & resistance (MSNR), long side, on daily bars.

MSNR draws levels at candle BODIES, not wicks. A V-level (support) is where a red candle is followed by a green one:
the red candle's close. A level is "fresh" until the price first comes back to it; MSNR trades that first touch.
Here: a fresh V-level whose first touch holds (the day's low reaches it, the close stays above) is a buy signal for
the next open, only in an uptrend ("storyline": close above the 200-day average, 50-day above 200-day). Stop half
an ATR below the level, target 2R, 20-day time stop (the engine's fixed-target policy)."""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from aitrader.backtest.types import LONG, Candidate


def fresh_support_touches(df: pd.DataFrame, max_age: int = 250) -> pd.Series:
    """For each day: the fresh V-level that the day's low touched while the close held above it (the highest one if
    several), else NaN. Levels are known only after the green candle's close, and are used up on any touch."""
    o, c, low = df["open"].to_numpy(float), df["close"].to_numpy(float), df["low"].to_numpy(float)
    out = np.full(len(df), np.nan)
    fresh: List[tuple] = []  # (level, day index from which it can be used)
    for t in range(len(df)):
        if t >= 2 and c[t - 2] < o[t - 2] and c[t - 1] > o[t - 1]:
            fresh.append((c[t - 2], t))
        fresh = [(lv, born) for lv, born in fresh if t - born < max_age]
        touched = [lv for lv, _ in fresh if low[t] <= lv]
        if touched:
            held = [lv for lv in touched if c[t] > lv]
            if held:
                out[t] = max(held)
            fresh = [(lv, born) for lv, born in fresh if low[t] > lv]
    return pd.Series(out, index=df.index, name="msnr_level")


class MalaysianSnR:
    key = "msnr"
    name = "Malaysian SnR (fresh support)"
    PARAMS = {"max_age": 250, "stop_atr": 0.5, "min_stop": 0.01, "max_stop": 0.08}

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["msnr_level"] = fresh_support_touches(out, self.PARAMS["max_age"])
        return out

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        if rows.empty or "msnr_level" not in rows.columns:
            return []
        c, level = rows["close"], rows["msnr_level"]
        stop = level - self.PARAMS["stop_atr"] * rows["atr14"]
        dist = (c - stop) / c
        mask = (level.notna() & (c > rows["sma200"]) & (rows["sma50"] > rows["sma200"])
                & (dist >= self.PARAMS["min_stop"]) & (dist <= self.PARAMS["max_stop"]))
        rng = (rows["high"] - rows["low"]).where(rows["high"] > rows["low"])
        score = ((c - rows["low"]) / rng).fillna(0.5)  # closed near the high = a stronger bounce
        out = []
        for symbol in sorted(rows.index[mask]):
            r = rows.loc[symbol]
            reason = (f"First touch of a fresh support (V-level {level[symbol]:.2f}, where a red day turned green): "
                      f"low {r['low']:.2f}, closed {r['close']:.2f} above it, in an uptrend. Stop {stop[symbol]:.2f} "
                      f"(½ ATR below the level), target 2R.")
            facts = {"level": round(float(level[symbol]), 4), "atr14": round(float(r["atr14"]), 4)}
            out.append(Candidate(str(symbol), LONG, day.date(), float(r["close"]), float(stop[symbol]),
                                 float(score[symbol]), reason, facts))
        return out
