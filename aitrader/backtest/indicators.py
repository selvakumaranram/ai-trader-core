"""Causal price indicators: every value at row t uses rows <= t only."""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_BARS = 200
MIN_TRADED_VALUE = 1e8  # ₹10 crore average daily traded value


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wilder(values: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing: seeded with the mean of the first n valid values, then (prev*(n-1) + x) / n."""
    v = values.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    run, start = 0, None
    for i, x in enumerate(v):
        run = run + 1 if not np.isnan(x) else 0
        if run == n:
            start = i
            break
    if start is not None:
        out[start] = v[start - n + 1:start + 1].mean()
        for i in range(start + 1, len(v)):
            out[i] = out[i - 1] if np.isnan(v[i]) else (out[i - 1] * (n - 1) + v[i]) / n
    return pd.Series(out, index=values.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
                     axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    avg_gain = wilder(delta.clip(lower=0), n)
    avg_loss = wilder((-delta).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + avg_gain / avg_loss)
    return out.mask((avg_loss == 0) & avg_gain.notna(), 100.0)


def prior_max(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).max().shift(1)


def prior_min(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).min().shift(1)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close, volume = out["close"], out["volume"].astype(float)
    out["bars"] = np.arange(1, len(out) + 1)
    for n in (20, 50, 200):
        out[f"sma{n}"] = sma(close, n)
    out["atr14"] = atr(out, 14)
    out["rsi14"] = rsi(close, 14)
    for n in (1, 3, 5, 21):
        out[f"ret{n}"] = close / close.shift(n) - 1
    out["vol5"] = sma(volume, 5)
    out["vol50"] = sma(volume, 50)
    out["vol20_prior"] = sma(volume, 20).shift(1)
    out["high20_prior"] = prior_max(out["high"], 20)
    out["low20_prior"] = prior_min(out["low"], 20)
    out["low3"] = out["low"].rolling(3, min_periods=3).min()
    out["high3"] = out["high"].rolling(3, min_periods=3).max()
    out["value20"] = sma(close * volume, 20)
    return out
