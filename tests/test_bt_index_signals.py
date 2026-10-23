from datetime import date

import pytest

from aitrader.backtest.index_signals import index_signals, index_summary
from aitrader.backtest.indicators import add_indicators
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.types import LONG, SHORT, IndexSignal, MoneyRules
from bt_helpers import flat, frame


def signals_for(rows):
    f = add_indicators(frame(rows))
    return index_signals(f, "NIFTY 50", list(f.index), Breakout(use_volume=False), MoneyRules())


def test_index_breakout_ignores_zero_volume():
    rows = flat(60, volume=0) + [(100, 103.5, 100, 103, 0), (103.2, 104, 102.5, 103.8, 0),
                                 (104, 112, 103.9, 111, 0)] + [(111, 111.5, 110.5, 111, 0)] * 5
    (s,) = signals_for(rows)
    assert (s.direction, s.exit_reason, s.entry) == (LONG, "target", 103.2)
    assert s.stop == pytest.approx(101 - 29.5 / 14)
    assert s.r_multiple == pytest.approx(2.0)


def test_index_breakdown_short():
    rows = flat(60, volume=0) + [(99.5, 100, 96.5, 97, 0), (96.8, 97.5, 96, 96.2, 0),
                                 (96, 96.1, 88, 89, 0)] + [(89, 89.5, 88.5, 89, 0)] * 5
    (s,) = signals_for(rows)
    assert (s.direction, s.exit_reason, s.entry) == (SHORT, "target", 96.8)
    assert s.stop == pytest.approx(99 + 29.5 / 14)
    assert s.r_multiple == pytest.approx(2.0)


def test_index_summary_whole_and_unseen():
    def sig(direction, r, day):
        return IndexSignal("NIFTY 50", direction, day, day, 100, 95, 110, day, 100, "target", r, "x")
    rows = index_summary([sig(LONG, 2.0, date(2024, 1, 2)), sig(LONG, -1.0, date(2025, 1, 2))], date(2025, 1, 1))
    assert {"symbol": "NIFTY 50", "direction": LONG, "period": "whole", "signals": 2, "win_rate": 0.5,
            "avg_r": 0.5} in rows
    assert {"symbol": "NIFTY 50", "direction": LONG, "period": "unseen", "signals": 1, "win_rate": 0.0,
            "avg_r": -1.0} in rows
