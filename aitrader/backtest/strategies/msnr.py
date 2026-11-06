"""Strategy M — Malaysian support & resistance (MSNR), long side, on daily bars.

MSNR draws levels at candle BODIES, not wicks. A V-level (support) is where a red candle is followed by a green one:
the red candle's close. A level is "fresh" until the price first comes back to it; MSNR trades that first touch.
Here: a fresh V-level whose first touch holds (the day's low reaches it, the close stays above) is a buy signal for
the next open, only in an uptrend ("storyline": close above the 200-day average, 50-day above 200-day). Stop half
an ATR below the level, target 2R, 20-day time stop (the engine's fixed-target policy).

The v2 research options in PARAMS (support quality, trend, entry, stop and exit variants) are all OFF by default,
so the baseline is unchanged."""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
import pandas as pd

from aitrader.backtest.indicators import true_range, wilder
from aitrader.backtest.types import LONG, Candidate

FACT_COLUMNS = ("red_body", "green_body", "green_relvol", "age", "moved_atr")


def _ok(value: float, minimum: Optional[float]) -> bool:
    return minimum is None or (not math.isnan(value) and value >= minimum)


def support_touches(df: pd.DataFrame, max_age: int = 250, red_body_min: Optional[float] = None,
                    green_body_min: Optional[float] = None, green_relvol_min: Optional[float] = None,
                    min_age: Optional[int] = None, moved_atr_min: Optional[float] = None) -> pd.DataFrame:
    """Per day: the fresh V-level whose first touch held (the highest if several) and its quality facts, else NaN.

    A level forms from a red day t-2 and a green day t-1 and is usable from day t. Formation filters (body/range
    of the red and green candles, the green day's volume ÷ its prior 20-day average) decide whether it is a level at
    all. Touch filters (age in days since usable, how far the close moved above it before the touch in ATRs) decide
    whether a touch is a signal; a touch always uses the level up, and a close below it breaks it."""
    o, h, low, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    vol = df["volume"].to_numpy(float)
    vol20 = pd.Series(vol, index=df.index).rolling(20, min_periods=20).mean().shift(1).to_numpy()
    atr_like = true_range(df).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()  # defined from the first bar
    rng = np.where(h > low, h - low, np.nan)
    out = np.full((len(df), 1 + len(FACT_COLUMNS)), np.nan)
    fresh: List[list] = []  # [level, usable-from index, red_body, green_body, green_relvol, highest close since]
    for t in range(len(df)):
        if t >= 2 and c[t - 2] < o[t - 2] and c[t - 1] > o[t - 1]:
            red = (o[t - 2] - c[t - 2]) / rng[t - 2] if not np.isnan(rng[t - 2]) else 0.0
            green = (c[t - 1] - o[t - 1]) / rng[t - 1] if not np.isnan(rng[t - 1]) else 0.0
            relvol = vol[t - 1] / vol20[t - 1] if vol20[t - 1] and not np.isnan(vol20[t - 1]) else np.nan
            if _ok(red, red_body_min) and _ok(green, green_body_min) and _ok(relvol, green_relvol_min):
                fresh.append([c[t - 2], t, red, green, relvol, -np.inf])
        fresh = [lv for lv in fresh if t - lv[1] < max_age]
        touched = [lv for lv in fresh if low[t] <= lv[0]]
        best = None
        for lv in touched:
            age = t - lv[1]
            moved = (max(lv[5], lv[0]) - lv[0]) / atr_like[t] if atr_like[t] > 0 else np.nan
            if c[t] > lv[0] and (min_age is None or age >= min_age) and _ok(moved, moved_atr_min):
                if best is None or lv[0] > best[0]:
                    best = (lv[0], lv[2], lv[3], lv[4], age, moved)
        if best is not None:
            out[t] = best
        fresh = [lv for lv in fresh if low[t] > lv[0]]
        for lv in fresh:
            lv[5] = max(lv[5], c[t])
    return pd.DataFrame(out, index=df.index, columns=("level",) + FACT_COLUMNS)


def fresh_support_touches(df: pd.DataFrame, max_age: int = 250) -> pd.Series:
    """The original rule's signal level per day (NaN when none)."""
    return support_touches(df, max_age)["level"].rename("msnr_level")


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's ADX(n)."""
    up, down = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = wilder(true_range(df), n)
    pdi, mdi = 100 * wilder(plus, n) / tr, 100 * wilder(minus, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx, n)


TRENDS = {
    "sma": lambda r: (r["close"] > r["sma200"]) & (r["sma50"] > r["sma200"]),  # the original storyline
    "ema20": lambda r: r["close"] > r["ema20"],
    "ema50": lambda r: r["close"] > r["ema50"],
    "ema100": lambda r: r["close"] > r["ema100"],
    "ema200": lambda r: r["close"] > r["ema200"],
    "ema50_200": lambda r: r["ema50"] > r["ema200"],
    "above_ema50_200": lambda r: (r["close"] > r["ema50"]) & (r["close"] > r["ema200"]),
}


class MalaysianSnR:
    key = "msnr"
    name = "Malaysian SnR (fresh support)"
    PARAMS = {"max_age": 250, "stop_atr": 0.5, "min_stop": 0.01, "max_stop": 0.08,
              # v2 research options (defaults = the original rules)
              "red_body_min": None, "green_body_min": None, "green_relvol_min": None, "min_age": None,
              "moved_atr_min": None, "trend": "sma", "adx_min": None, "bullish_close": False, "stop_mode": "level",
              "entry_confirm": False, "exit_policy": "fixed_target", "partial_r": 1.0, "time_stop_days": 20,
              "trail_atr": 3.0}

    @property
    def exit_policy(self) -> str:
        return self.PARAMS.get("exit_policy", "fixed_target")

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.PARAMS
        out = df.copy()
        touches = support_touches(out, p["max_age"], p["red_body_min"], p["green_body_min"], p["green_relvol_min"],
                                  p["min_age"], p["moved_atr_min"])
        out["msnr_level"] = touches["level"]
        for k in FACT_COLUMNS:
            out[f"msnr_{k}"] = touches[k]
        out["low5"] = out["low"].rolling(5, min_periods=5).min()
        out["high5"] = out["high"].rolling(5, min_periods=5).max()
        for n in (20, 50, 100, 200):
            out[f"ema{n}"] = out["close"].ewm(span=n, adjust=False, min_periods=n).mean()
        out["adx14"] = adx(out)
        return out

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        if rows.empty or "msnr_level" not in rows.columns:
            return []
        p = self.PARAMS
        c, level, atr = rows["close"], rows["msnr_level"], rows["atr14"]
        if p["stop_mode"] == "structure":  # below the touch day's low
            stop = rows["low"] - 0.25 * atr
        else:
            stop = level - p["stop_atr"] * atr
        dist = (c - stop) / c
        mask = level.notna() & TRENDS[p["trend"]](rows) & (dist >= p["min_stop"]) & (dist <= p["max_stop"])
        if p["adx_min"] is not None:
            mask &= rows["adx14"] >= p["adx_min"]
        rng = (rows["high"] - rows["low"]).where(rows["high"] > rows["low"])
        position = ((c - rows["low"]) / rng).fillna(0.5)
        if p["bullish_close"]:
            mask &= (c > rows["open"]) & (position >= 2 / 3)
        out = []
        for symbol in sorted(rows.index[mask]):
            r = rows.loc[symbol]
            reason = (f"First touch of a fresh support (V-level {level[symbol]:.2f}, where a red day turned green): "
                      f"low {r['low']:.2f}, closed {r['close']:.2f} above it, in an uptrend. Stop {stop[symbol]:.2f}.")
            facts = {"level": round(float(level[symbol]), 4), "atr14": round(float(r["atr14"]), 4),
                     "signal_high": round(float(r["high"]), 4)}
            for k in FACT_COLUMNS:
                v = r.get(f"msnr_{k}", np.nan)
                facts[k] = None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)
            out.append(Candidate(str(symbol), LONG, day.date(), float(r["close"]), float(stop[symbol]),
                                 float(position[symbol]), reason, facts))
        return out
