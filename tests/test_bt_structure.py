import warnings

import numpy as np
import pandas as pd
import pytest

from aitrader.backtest.indicators import add_indicators
from aitrader.backtest.structure import add_structure, bull_engulf, hammer, known_swings, levels_at, trend
from bt_helpers import flat, frame


def bars_from_closes(closes, spread=0.5, volume=1e6):
    return frame([(c, c + spread, c - spread, c, volume) for c in closes])


def test_swing_high_is_unknown_until_two_bars_later():
    df = frame([(h - 0.2, h, h - 0.5, h - 0.2, 1) for h in [1, 2, 5, 2, 1, 1]])
    ks = known_swings(df)
    assert np.isnan(ks["sh2"].iloc[3])
    assert ks["sh2"].iloc[4] == 5


RISING = [100, 101, 102, 101, 100.5, 102, 103, 104, 103, 102, 104, 105, 106, 105, 104,
          106, 107, 108, 107, 106, 108, 109]


def test_trend_up_down_sideways():
    up = bars_from_closes(RISING)
    assert trend(known_swings(up).join(up["close"])).iloc[-1] == 1
    down = bars_from_closes([300 - c for c in RISING])
    assert trend(known_swings(down).join(down["close"])).iloc[-1] == -1
    flat_df = frame(flat(22))
    assert trend(known_swings(flat_df).join(flat_df["close"])).iloc[-1] == 0


def test_levels_cluster_within_one_percent():
    got = levels_at([100, 100.5, 120, 130, 130.9])
    assert [(round(lvl, 2), n) for lvl, n in got] == [(100.25, 2), (130.45, 2)]


def test_hammer_and_engulfing():
    df = frame([(100, 101, 94, 100.8, 1), (100, 101.5, 99.5, 101, 1)])
    assert list(hammer(df)) == [True, False]
    pair = frame([(101, 101.5, 98.8, 99, 1), (98.5, 101.8, 98.4, 101.5, 1)])
    assert list(bull_engulf(pair)) == [False, True]


def test_zero_range_is_not_a_hammer():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert not hammer(frame([(100, 100, 100, 100, 1)])).iloc[0]


def structured(rows, use_volume=True):
    return add_structure(add_indicators(frame(rows)), use_volume=use_volume)


def test_choppy_needs_250_bars():
    assert not structured(flat(200))["choppy"].any()


def test_choppy_flags_quietest_fifth():
    rows = [(100, 102, 98, 100, 1e6)] * 270 + [(100, 100.2, 99.8, 100, 1e6)] * 30
    assert structured(rows)["choppy"].iloc[-1]


BREAKOUT = [(100.5, 103.2, 100.4, 103, 3e6)]


def test_breakout_retest_long():
    rows = flat(25, volume=1e6) + BREAKOUT + [(102.5, 102.8, 101.2, 102.5, 1e6), (102, 102.6, 101.1, 102.2, 1e6)]
    s = structured(rows)
    assert s["retest_long"].iloc[26] and s["retest_level"].iloc[26] == 101
    assert not s["retest_long"].iloc[27]


def test_breakout_requires_first_close_above():
    rows = flat(25, volume=1e6) + BREAKOUT + [(103, 104, 102.9, 103.5, 3e6), (102, 102.4, 101.3, 102, 1e6)]
    s = structured(rows)
    assert s["retest_long"].iloc[27] and s["retest_level"].iloc[27] == 101


def test_retest_ignores_volume_when_disabled():
    rows = [(o, h, low, c, 0) for o, h, low, c, _ in
            flat(25) + BREAKOUT + [(102.5, 102.8, 101.2, 102.5, 0)]]
    assert structured(rows, use_volume=False)["retest_long"].iloc[26]
    assert not structured(rows, use_volume=True)["retest_long"].iloc[26]


def test_add_structure_is_causal():
    rng = np.random.default_rng(3)
    closes = 1000 * np.cumprod(1 + rng.normal(0.0005, 0.02, 320))
    rows = [(c * 0.998, c * 1.01, c * 0.99, c, 1e6) for c in closes]
    base = structured(rows)
    changed = frame(rows)
    changed.loc[changed.index[261:], ["open", "high", "low", "close"]] *= 1.5
    other = add_structure(add_indicators(changed))
    pd.testing.assert_frame_equal(base.iloc[:261], other.iloc[:261])


@pytest.mark.parametrize("col", ["ema20", "sh1", "sh2", "sl1", "sl2", "trend", "support", "support_touches",
                                 "resistance", "resistance_touches", "hammer", "shooting_star", "bull_engulf",
                                 "bear_engulf", "choppy", "retest_long", "retest_short", "retest_level", "low5",
                                 "high5"])
def test_add_structure_columns(col):
    assert col in structured(flat(30)).columns
