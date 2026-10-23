import math

import pandas as pd
import pytest

from aitrader.backtest.bars import frame_from_bars
from aitrader.backtest.indicators import add_indicators, atr, prior_max, prior_min, rsi, sma, wilder
from aitrader.models import Bar
from bt_helpers import flat, frame


def s(values):
    return pd.Series(values, dtype=float)


def test_sma_needs_full_window():
    out = sma(s([1, 2, 3, 4, 5]), 3)
    assert math.isnan(out[1]) and list(out[2:]) == [2, 3, 4]


def test_wilder_seeds_with_mean_then_smooths():
    out = wilder(s([1, 2, 3, 4, 5]), 3)
    assert math.isnan(out[1])
    assert out[2] == pytest.approx(2) and out[3] == pytest.approx(8 / 3) and out[4] == pytest.approx(31 / 9)


def test_atr_uses_true_range():
    df = frame([(9, 10, 8, 9, 1), (10, 11, 9, 10, 1), (11, 12, 10, 11, 1), (8, 11, 7, 8, 1)])
    out = atr(df, 3)
    assert out.iloc[2] == pytest.approx(2) and out.iloc[3] == pytest.approx(8 / 3)


def test_rsi_wilder():
    out = rsi(s([1, 2, 3, 2, 3]), 2)
    assert out[2] == pytest.approx(100) and out[3] == pytest.approx(50) and out[4] == pytest.approx(75)


def test_prior_window_excludes_today():
    assert list(prior_max(s([1, 5, 3, 4]), 2)[2:]) == [5, 5]
    assert list(prior_min(s([4, 1, 3, 2]), 2)[2:]) == [1, 1]


def test_add_indicators_columns_and_counts():
    df = add_indicators(frame(flat(210)))
    row = df.iloc[-1]
    assert row["bars"] == 210 and row["sma200"] == pytest.approx(100)
    assert row["atr14"] == pytest.approx(2) and row["high20_prior"] == 101 and row["low20_prior"] == 99
    assert row["value20"] == pytest.approx(2e8)


def test_frame_from_bars():
    from datetime import date
    df = frame_from_bars([Bar(date(2024, 1, 2), 1, 2, 0.5, 1.5, 10)])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0] == pd.Timestamp("2024-01-02") and df.iloc[0]["close"] == 1.5
