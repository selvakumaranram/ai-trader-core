from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, List, Protocol, Tuple

from aitrader.models import Bar, CorporateAction


class DailyBarSource(Protocol):
    name: str
    price_basis: str

    def fetch(self, symbol: str, start: date, end: date) -> Tuple[List[Bar], List[CorporateAction]]: ...


def _yf_history(symbol: str, start: date, end_exclusive: date):
    import yfinance as yf

    return yf.Ticker(symbol).history(start=start.isoformat(), end=end_exclusive.isoformat(), interval="1d",
                                     auto_adjust=False, actions=True)


def _is_nan(value) -> bool:
    return value != value  # NaN is the only value not equal to itself


class YFinanceSource:
    """Yahoo daily bars. Close with auto_adjust=False is split-adjusted (verified 2026-10-20)."""

    name = "yfinance"
    price_basis = "split_adjusted"

    def __init__(self, history_fn: Callable = _yf_history):
        self._history = history_fn

    def fetch(self, symbol: str, start: date, end: date) -> Tuple[List[Bar], List[CorporateAction]]:
        frame = self._history(symbol, start, end + timedelta(days=1))
        if frame is None or frame.empty:
            return [], []
        bars, actions = [], []
        for ts, row in frame.iterrows():
            day = ts.date()
            prices = [row["Open"], row["High"], row["Low"], row["Close"]]
            if not any(_is_nan(p) for p in prices):
                volume = row.get("Volume", 0)
                bars.append(Bar(day, float(prices[0]), float(prices[1]), float(prices[2]), float(prices[3]),
                                0 if _is_nan(volume) else int(volume)))
            split = row.get("Stock Splits", 0.0)
            if split and not _is_nan(split):
                actions.append(CorporateAction(day, "split", 1.0, float(split)))
        return bars, actions
