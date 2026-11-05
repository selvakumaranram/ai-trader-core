"""Candidate "strong day" rules for NIFTY 50, decided on the daily close, plus a replay over history.

Every rule shares one exit plan on the underlying: stop = close ∓ 1 × ATR(14), target = close ± 2 × ATR(14).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

import pandas as pd

from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.price_action import PriceAction
from aitrader.backtest.strategies.pullback import Pullback
from aitrader.backtest.types import LONG, SHORT

BIG_MOVE = 0.015
GAP = 0.01
DIP_ATR = 2.0
STOP_ATR, TARGET_ATR = 1.0, 2.0
HOLD_DAYS = 5


@dataclass(frozen=True)
class ShadowSignal:
    rule: str
    side: str  # "CE" | "PE"
    close: float
    stop: float
    target: float
    reason: str
    facts: dict = field(default_factory=dict, compare=False, hash=False)


class Context:
    """One NIFTY frame (with add_indicators columns) and the price-action structure, prepared once."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self._d: Optional[pd.DataFrame] = None
        self.voters = {"B": (Pullback(), False), "C": (Breakout(use_volume=False), False),
                       "D": (PriceAction(use_volume=False), True)}

    @property
    def d_frame(self) -> pd.DataFrame:
        if self._d is None:
            self._d = PriceAction(use_volume=False).prepare(self.df)
        return self._d


def _ctx(x) -> Context:
    return x if isinstance(x, Context) else Context(x)


def _signal(rule: str, side: str, row: pd.Series, reason: str, facts: dict) -> List[ShadowSignal]:
    close, atr = float(row["close"]), float(row["atr14"])
    if atr != atr or atr <= 0:
        return []
    stop = close - STOP_ATR * atr if side == "CE" else close + STOP_ATR * atr
    target = close + TARGET_ATR * atr if side == "CE" else close - TARGET_ATR * atr
    return [ShadowSignal(rule, side, close, stop, target, reason, {**facts, "atr14": round(atr, 2)})]


def big_move(x, i: int = -1) -> List[ShadowSignal]:
    df = _ctx(x).df
    row, prev = df.iloc[i], df.iloc[i - 1]
    change = float(row["close"]) / float(prev["close"]) - 1
    rng = float(row["high"]) - float(row["low"])
    if rng <= 0:
        return []
    where = (float(row["close"]) - float(row["low"])) / rng
    facts = {"change": round(change, 4), "close_in_range": round(where, 2)}
    if change >= BIG_MOVE and where >= 0.75:
        return _signal("big_move", "CE", row, f"NIFTY closed {change:+.1%} near the day's high.", facts)
    if change <= -BIG_MOVE and where <= 0.25:
        return _signal("big_move", "PE", row, f"NIFTY closed {change:+.1%} near the day's low.", facts)
    return []


def gap_hold(x, i: int = -1) -> List[ShadowSignal]:
    df = _ctx(x).df
    row, prev = df.iloc[i], df.iloc[i - 1]
    gap = float(row["open"]) / float(prev["close"]) - 1
    facts = {"gap": round(gap, 4)}
    if gap >= GAP and float(row["close"]) >= float(row["open"]):
        return _signal("gap_hold", "CE", row, f"NIFTY gapped up {gap:+.1%} and held it to the close.", facts)
    if gap <= -GAP and float(row["close"]) <= float(row["open"]):
        return _signal("gap_hold", "PE", row, f"NIFTY gapped down {gap:+.1%} and held it to the close.", facts)
    return []


def sharp_dip(x, i: int = -1) -> List[ShadowSignal]:
    df = _ctx(x).df
    row = df.iloc[i]
    c, s20, s200, atr = (float(row[k]) for k in ("close", "sma20", "sma200", "atr14"))
    if any(v != v for v in (s20, s200, atr)):
        return []
    facts = {"sma20": round(s20, 2), "sma200": round(s200, 2)}
    if c > s200 and c <= s20 - DIP_ATR * atr:
        return _signal("sharp_dip", "CE", row, "Uptrend (above the 200-day average) but a sharp dip, 2 ATR "
                                               "below the 20-day average: a bounce bet.", facts)
    if c < s200 and c >= s20 + DIP_ATR * atr:
        return _signal("sharp_dip", "PE", row, "Downtrend (below the 200-day average) but a sharp rally, 2 ATR "
                                               "above the 20-day average: a fade bet.", facts)
    return []


def agree2_side(longs: Sequence[str], shorts: Sequence[str], *, close: float, sma200: float) -> Optional[str]:
    if len(longs) >= 2 and close > sma200:
        return "CE"
    if len(shorts) >= 2 and close < sma200:
        return "PE"
    return None


def agree2(x, i: int = -1) -> List[ShadowSignal]:
    ctx = _ctx(x)
    df = ctx.df
    day = df.index[i]
    row = df.iloc[i]
    votes = {LONG: [], SHORT: []}
    for name, (strategy, needs_structure) in ctx.voters.items():
        source = ctx.d_frame if needs_structure else df
        rows = source.iloc[[i]].rename(index={day: "NIFTY 50"})
        for direction in (LONG, SHORT):
            if strategy.signals(day, rows, direction):
                votes[direction].append(name)
    sma200 = float(row["sma200"])
    if sma200 != sma200:
        return []
    side = agree2_side(votes[LONG], votes[SHORT], close=float(row["close"]), sma200=sma200)
    if side is None:
        return []
    names = votes[LONG] if side == "CE" else votes[SHORT]
    return _signal("agree2", side, row, f"{len(names)} of the B/C/D index rules agree ({', '.join(names)}) "
                                        f"and NIFTY is {'above' if side == 'CE' else 'below'} its 200-day average.",
                   {"rules": names})


RULES = {"agree2": agree2, "big_move": big_move, "gap_hold": gap_hold, "sharp_dip": sharp_dip}


def evaluate(df: pd.DataFrame, i: int = -1) -> List[ShadowSignal]:
    ctx = Context(df)
    return [s for fn in RULES.values() for s in fn(ctx, i)]


def replay(df: pd.DataFrame, unseen_from: date, rules: Optional[Sequence[str]] = None,
           hold: int = HOLD_DAYS) -> Dict[str, dict]:
    """Every rule over the history: next-day-open entry, outcome after `hold` days, and whether the underlying
    reached the target or the stop first (a day touching both counts as the stop)."""
    ctx = Context(df)
    keys = list(rules or RULES)
    acc = {k: {"earlier": [], "unseen": []} for k in keys}
    for i in range(1, len(df) - hold):
        for k in keys:
            for s in RULES[k](ctx, i):
                entry = float(df["open"].iloc[i + 1])
                path = df.iloc[i + 1:i + hold + 1]
                sign = 1 if s.side == "CE" else -1
                move = sign * (float(df["close"].iloc[i + hold]) / entry - 1)
                first = "neither"
                for _, bar in path.iterrows():
                    hit_stop = bar["low"] <= s.stop if s.side == "CE" else bar["high"] >= s.stop
                    hit_target = bar["high"] >= s.target if s.side == "CE" else bar["low"] <= s.target
                    if hit_stop or hit_target:
                        first = "stop" if hit_stop else "target"
                        break
                period = "unseen" if df.index[i].date() >= unseen_from else "earlier"
                acc[k][period].append((move, first))
    out = {}
    for k, periods in acc.items():
        out[k] = {}
        for period, rows in periods.items():
            n = len(rows)
            out[k][period] = {
                "signals": n,
                "right_direction": sum(1 for m, _ in rows if m > 0) / n if n else None,
                "avg_move": sum(m for m, _ in rows) / n if n else None,
                "target_first": sum(1 for _, f in rows if f == "target") / n if n else None,
                "stop_first": sum(1 for _, f in rows if f == "stop") / n if n else None,
            }
    return out
