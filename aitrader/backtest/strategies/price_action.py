"""Strategy D — price action: market structure, pullback to support / breakout-retest, room for at least 2R."""
from __future__ import annotations

import math
from typing import List, Optional

import pandas as pd

from aitrader.backtest.structure import add_structure
from aitrader.backtest.types import LONG, SHORT, Candidate


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _near(price: float, level, band: float) -> bool:
    return _ok(level) and float(level) > 0 and abs(price / float(level) - 1) <= band


class PriceAction:
    key = "price_action"
    name = "Price action"
    exit_policy = "partial_trail"
    DEFAULT_MONEY = {"risk_per_trade": 300.0, "daily_loss_limit": 600.0}
    PARAMS = {"swing_bars": 2, "level_lookback": 60, "level_tolerance": 0.01, "min_touches": 2,
              "near_band": 0.01, "min_stop": 0.01, "max_stop": 0.08, "range_bars": 20, "range_max_span": 0.12,
              "volume_surge": 1.5, "retest_bars": 5, "retest_atr": 0.5, "choppy_lookback": 250,
              "choppy_pct": 0.20, "min_room_r": 2.0, "room_cap_r": 5.0, "partial_r": 1.5, "time_stop_days": 30,
              "trail_bars": 5}

    def __init__(self, use_volume: bool = True):
        self.use_volume = use_volume

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return add_structure(df, self.use_volume)

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        return self.signals(day, rows, LONG)

    def signals(self, day: pd.Timestamp, rows: pd.DataFrame, direction: str) -> List[Candidate]:
        if rows.empty:
            return []
        out = []
        for symbol in sorted(rows.index):
            cand = self._check(day, str(symbol), rows.loc[symbol], direction == LONG)
            if cand is not None:
                out.append(cand)
        return out

    def _setups(self, r: pd.Series, long: bool) -> list:
        """(setup, stop, level, touches, text) for each setup present on this row; retest first."""
        found = []
        if bool(r["retest_long"] if long else r["retest_short"]) and _ok(r["retest_level"]):
            level, atr = float(r["retest_level"]), float(r["atr14"])
            if long:
                stop = min(float(r["low"]), level - self.PARAMS["retest_atr"] * atr)
                text = f"Broke out above the range high {level:.2f}, came back to retest it and closed above."
            else:
                stop = max(float(r["high"]), level + self.PARAMS["retest_atr"] * atr)
                text = f"Broke down below the range low {level:.2f}, came back to retest it and closed below."
            found.append(("retest", stop, level, None, f"{text} Stop {stop:.2f}."))
        if int(r["trend"]) == (1 if long else -1) and self._pullback_ok(r, long):
            found.append(self._pullback(r, long))
        return found

    def _check(self, day: pd.Timestamp, symbol: str, r: pd.Series, long: bool) -> Optional[Candidate]:
        p = self.PARAMS
        if bool(r["choppy"]):
            return None
        close = float(r["close"])
        blocker = r["resistance"] if long else r["support"]
        for setup, stop, level, touches, text in self._setups(r, long):
            risk = (close - stop) if long else (stop - close)
            if not (p["min_stop"] <= risk / close <= p["max_stop"]):
                continue
            room = None
            if _ok(blocker):
                room = ((float(blocker) - close) if long else (close - float(blocker))) / risk
                if room < p["min_room_r"]:
                    continue
            capped = p["room_cap_r"] if room is None else min(room, p["room_cap_r"])
            room_text = "No level in the way." if room is None else f"Room to the next level: {room:.1f}R."
            facts = {"setup": setup, "room_r": None if room is None else round(room, 3),
                     "level": None if level is None else round(level, 4), "touches": touches,
                     "close": round(close, 4)}
            return Candidate(symbol, LONG if long else SHORT, day.date(), close, float(stop),
                             float(capped * 10 + (1 if setup == "retest" else 0)), f"{text} {room_text}", facts)
        return None

    def _pullback_ok(self, r: pd.Series, long: bool) -> bool:
        band = self.PARAMS["near_band"]
        close, ema = float(r["close"]), float(r["ema20"])
        if long:
            touched = _near(float(r["low"]), r["support"], band) or _near(float(r["low"]), ema, band)
            return touched and close > ema and (bool(r["hammer"]) or bool(r["bull_engulf"]))
        touched = _near(float(r["high"]), r["resistance"], band) or _near(float(r["high"]), ema, band)
        return touched and close < ema and (bool(r["shooting_star"]) or bool(r["bear_engulf"]))

    def _pullback(self, r: pd.Series, long: bool) -> tuple:
        band = self.PARAMS["near_band"]
        if long:
            at_level = _near(float(r["low"]), r["support"], band)
            level = float(r["support"]) if at_level else float(r["ema20"])
            touches = int(r["support_touches"]) if at_level else None
            where = f"support {level:.2f} ({touches} touches)" if at_level else f"the 20-day EMA {level:.2f}"
            candle = "hammer" if bool(r["hammer"]) else "bullish engulfing candle"
            text = (f"Uptrend (higher highs {float(r['sh1']):.2f} → {float(r['sh2']):.2f}, higher lows "
                    f"{float(r['sl1']):.2f} → {float(r['sl2']):.2f}). Pulled back to {where} and printed a "
                    f"{candle}. Stop {float(r['low']):.2f} (candle low).")
            return ("pullback", float(r["low"]), level, touches, text)
        at_level = _near(float(r["high"]), r["resistance"], band)
        level = float(r["resistance"]) if at_level else float(r["ema20"])
        touches = int(r["resistance_touches"]) if at_level else None
        where = f"resistance {level:.2f} ({touches} touches)" if at_level else f"the 20-day EMA {level:.2f}"
        candle = "shooting star" if bool(r["shooting_star"]) else "bearish engulfing candle"
        text = (f"Downtrend (lower highs {float(r['sh1']):.2f} → {float(r['sh2']):.2f}, lower lows "
                f"{float(r['sl1']):.2f} → {float(r['sl2']):.2f}). Rallied to {where} and printed a {candle}. "
                f"Stop {float(r['high']):.2f} (candle high).")
        return ("pullback", float(r["high"]), level, touches, text)
