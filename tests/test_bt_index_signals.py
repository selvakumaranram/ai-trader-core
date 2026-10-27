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


class _OneLong:
    exit_policy = "partial_trail"
    PARAMS = {"partial_r": 1.5, "time_stop_days": 30, "min_stop": 0.01, "max_stop": 0.08}

    def signals(self, day, rows, direction):
        from aitrader.backtest.types import Candidate
        if direction == LONG and day == self.signal_day:
            return [Candidate(rows.index[0], LONG, day.date(), 100.0, 95.0, 1.0, "test", {})]
        return []


def test_index_partial_trail_r_is_average_of_halves():
    f = add_indicators(frame(flat(3) + [(101, 108, 100.5, 107, 0), (106, 106.5, 99.9, 100.2, 0), (100, 101, 99, 100, 0)]))
    strat = _OneLong()
    strat.signal_day = f.index[1]
    (s,) = index_signals(f, "NIFTY 50", list(f.index), strat, MoneyRules())
    assert s.exit_reason == "trail_stop" and s.r_multiple == pytest.approx(0.75)
