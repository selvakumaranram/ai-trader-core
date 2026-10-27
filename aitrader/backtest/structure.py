"""Causal price-structure features for strategy D (price action).

Every column at row t uses bars <= t only. Swing points are found with a centred window, so a swing at bar i is
only *known* from bar i + SWING_BARS onward; everything derived from swings respects that delay.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from aitrader.backtest.indicators import atr

SWING_BARS = 2
LEVEL_LOOKBACK = 60
LEVEL_TOLERANCE = 0.01
MIN_TOUCHES = 2
CHOPPY_LOOKBACK = 250
CHOPPY_PCT = 0.20
RANGE_BARS = 20
RANGE_MAX_SPAN = 0.12
RETEST_BARS = 5
RETEST_BAND = 0.01
VOLUME_SURGE = 1.5


def swing_points(high: pd.Series, low: pd.Series, n: int = SWING_BARS) -> Tuple[pd.Series, pd.Series]:
    """Bool series marking swing highs/lows *at bar i* (uses bars i-n..i+n; callers must delay by n)."""
    offsets = [k for k in range(-n, n + 1) if k != 0]
    other_high = pd.concat([high.shift(k) for k in offsets], axis=1).max(axis=1, skipna=False)
    other_low = pd.concat([low.shift(k) for k in offsets], axis=1).min(axis=1, skipna=False)
    return high > other_high, low < other_low


def known_swings(df: pd.DataFrame, n: int = SWING_BARS) -> pd.DataFrame:
    """Previous (sh1/sl1) and latest (sh2/sl2) swing high/low prices known at each bar."""
    sh, sl = swing_points(df["high"], df["low"], n)
    out = pd.DataFrame(index=df.index)
    for prev_col, last_col, prices in (("sh1", "sh2", df["high"].where(sh)), ("sl1", "sl2", df["low"].where(sl))):
        known = prices.shift(n).dropna()
        out[last_col] = known.reindex(df.index).ffill()
        out[prev_col] = known.shift(1).reindex(df.index).ffill()
    return out[["sh1", "sh2", "sl1", "sl2"]]


def trend(df: pd.DataFrame) -> pd.Series:
    """1 = uptrend, -1 = downtrend, 0 = sideways, from sh1/sh2/sl1/sl2 and close."""
    up = (df["sh2"] > df["sh1"]) & (df["sl2"] > df["sl1"]) & (df["close"] > df["sl2"])
    down = (df["sh2"] < df["sh1"]) & (df["sl2"] < df["sl1"]) & (df["close"] < df["sh2"])
    return pd.Series(np.select([up, down], [1, -1], 0), index=df.index)


def levels_at(prices: Sequence[float], tolerance: float = LEVEL_TOLERANCE,
              min_touches: int = MIN_TOUCHES) -> List[Tuple[float, int]]:
    """Cluster prices lying within `tolerance` of a cluster's first price; keep clusters with enough touches."""
    out: List[Tuple[float, int]] = []
    cluster: List[float] = []
    for p in sorted(prices):
        if cluster and p > cluster[0] * (1 + tolerance):
            if len(cluster) >= min_touches:
                out.append((sum(cluster) / len(cluster), len(cluster)))
            cluster = []
        cluster.append(p)
    if len(cluster) >= min_touches:
        out.append((sum(cluster) / len(cluster), len(cluster)))
    return out


def _parts(df: pd.DataFrame):
    o, h, low, c = df["open"], df["high"], df["low"], df["close"]
    return o, h, low, c, (c - o).abs(), h - low


def hammer(df: pd.DataFrame) -> pd.Series:
    o, h, low, c, body, rng = _parts(df)
    lower_wick = np.minimum(o, c) - low
    return (rng > 0) & (lower_wick >= 2 * body) & ((c - low) * 3 >= rng * 2)


def shooting_star(df: pd.DataFrame) -> pd.Series:
    o, h, low, c, body, rng = _parts(df)
    upper_wick = h - np.maximum(o, c)
    return (rng > 0) & (upper_wick >= 2 * body) & ((h - c) * 3 >= rng * 2)


def bull_engulf(df: pd.DataFrame) -> pd.Series:
    o, c = df["open"], df["close"]
    po, pc = o.shift(1), c.shift(1)
    return (c > o) & (pc < po) & (o <= pc) & (c >= po)


def bear_engulf(df: pd.DataFrame) -> pd.Series:
    o, c = df["open"], df["close"]
    po, pc = o.shift(1), c.shift(1)
    return (c < o) & (pc > po) & (o >= pc) & (c <= po)


def _levels(df: pd.DataFrame, sh: pd.Series, sl: pd.Series) -> pd.DataFrame:
    """Nearest qualifying support (<= close) and resistance (>= close) from swings known at each bar."""
    high, low, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    idx_h, idx_l = np.flatnonzero(sh.to_numpy()), np.flatnonzero(sl.to_numpy())
    idx_all = np.concatenate([idx_h, idx_l])
    order = np.argsort(idx_all, kind="stable")
    idx = idx_all[order]
    price = np.concatenate([high[idx_h], low[idx_l]])[order]
    n = len(df)
    sup, sup_n, res, res_n = (np.full(n, np.nan) for _ in range(4))
    for t in range(n):
        lo = np.searchsorted(idx, t - LEVEL_LOOKBACK + 1, side="left")
        hi = np.searchsorted(idx, t - SWING_BARS, side="right")
        if hi - lo < MIN_TOUCHES:
            continue
        for level, touches in levels_at(price[lo:hi]):
            if level <= close[t] and (np.isnan(sup[t]) or level > sup[t]):
                sup[t], sup_n[t] = level, touches
            if level >= close[t] and (np.isnan(res[t]) or level < res[t]):
                res[t], res_n[t] = level, touches
    return pd.DataFrame({"support": sup, "support_touches": sup_n, "resistance": res,
                         "resistance_touches": res_n}, index=df.index)


def _choppy(df: pd.DataFrame) -> pd.Series:
    """Today's ATR(10)/close is in the lowest CHOPPY_PCT of its previous CHOPPY_LOOKBACK values."""
    ratio = atr(df, 10) / df["close"]
    below = ratio.rolling(CHOPPY_LOOKBACK + 1, min_periods=CHOPPY_LOOKBACK + 1).apply(
        lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    return (below < CHOPPY_PCT).fillna(False).astype(bool)


def _retests(df: pd.DataFrame, use_volume: bool) -> pd.DataFrame:
    high, low, close = df["high"], df["low"], df["close"]
    range_high = high.rolling(RANGE_BARS, min_periods=RANGE_BARS).max().shift(1)
    range_low = low.rolling(RANGE_BARS, min_periods=RANGE_BARS).min().shift(1)
    narrow = (range_high - range_low) / range_low <= RANGE_MAX_SPAN
    if use_volume:
        avg = df["vol20_prior"]
        vol_ok = (avg > 0) & (df["volume"] >= VOLUME_SURGE * avg)
    else:
        vol_ok = pd.Series(True, index=df.index)
    up = (close > range_high) & ~(close.shift(1) > range_high.shift(1)) & narrow & vol_ok
    down = (close < range_low) & ~(close.shift(1) < range_low.shift(1)) & narrow & vol_ok

    n = len(df)
    lo_a, hi_a, c_a = low.to_numpy(), high.to_numpy(), close.to_numpy()
    is_long, is_short = np.zeros(n, bool), np.zeros(n, bool)
    level = np.full(n, np.nan)
    for b in np.flatnonzero(up.to_numpy()):
        lvl = range_high.iloc[b]
        for t in range(b + 1, min(b + RETEST_BARS + 1, n)):
            if abs(lo_a[t] / lvl - 1) <= RETEST_BAND and c_a[t] > lvl:
                if not is_long[t]:
                    is_long[t], level[t] = True, lvl
                break
    for b in np.flatnonzero(down.to_numpy()):
        lvl = range_low.iloc[b]
        for t in range(b + 1, min(b + RETEST_BARS + 1, n)):
            if abs(hi_a[t] / lvl - 1) <= RETEST_BAND and c_a[t] < lvl:
                if not is_short[t] and not is_long[t]:
                    is_short[t], level[t] = True, lvl
                break
    return pd.DataFrame({"retest_long": is_long, "retest_short": is_short, "retest_level": level},
                        index=df.index)


def add_structure(df: pd.DataFrame, use_volume: bool = True) -> pd.DataFrame:
    """Add strategy D's structure columns; `df` must already carry the add_indicators columns."""
    out = df.copy()
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out = out.join(known_swings(out))
    out["trend"] = trend(out)
    sh, sl = swing_points(out["high"], out["low"])
    out = out.join(_levels(out, sh, sl))
    out["hammer"] = hammer(out)
    out["shooting_star"] = shooting_star(out)
    out["bull_engulf"] = bull_engulf(out).fillna(False).astype(bool)
    out["bear_engulf"] = bear_engulf(out).fillna(False).astype(bool)
    out["choppy"] = _choppy(out)
    out = out.join(_retests(out, use_volume))
    out["low5"] = out["low"].rolling(5, min_periods=5).min()
    out["high5"] = out["high"].rolling(5, min_periods=5).max()
    return out
