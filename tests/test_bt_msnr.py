"""Strategy M: Malaysian support & resistance (MSNR) — fresh V-level support, first touch, trend-filtered."""
import math

import pandas as pd
import pytest

from aitrader.backtest.indicators import add_indicators
from aitrader.backtest.strategies import STRATEGIES
from aitrader.backtest.strategies.msnr import MalaysianSnR, fresh_support_touches
from bt_helpers import frame

GREEN = (100, 103, 99.5, 102, 1e6)


def bars(*rows):
    return frame(list(rows))


def test_a_red_then_green_pair_makes_a_v_level_at_the_red_close_and_its_first_touch_signals():
    df = bars(GREEN, (102, 102.5, 98, 99, 1e6),   # red: closes 99
              (99, 104, 98.8, 103, 1e6),            # green: V-level 99 is now known (after this close)
              (103, 106, 102, 105, 1e6),
              (105, 105.5, 98.5, 101, 1e6))         # dips to 98.5 (touches 99), closes above it
    out = fresh_support_touches(df)
    assert list(out.isna()) == [True, True, True, True, False] and out.iloc[4] == 99.0


def test_the_level_is_not_used_on_the_day_it_forms():
    df = bars(GREEN, (102, 102.5, 98, 99, 1e6), (99, 104, 98.5, 103, 1e6))  # the green day's own low is below 99
    assert fresh_support_touches(df).isna().all()


def test_a_used_level_does_not_signal_again():
    df = bars(GREEN, (102, 102.5, 98, 99, 1e6), (99, 104, 98.8, 103, 1e6), (103, 106, 102, 105, 1e6),
              (105, 105.5, 98.5, 101, 1e6),          # first touch: signal
              (101, 101.5, 100.5, 100.8, 1e6),       # small red day: no new V-level
              (100.8, 104, 98.9, 102, 1e6))          # second touch of 99: the level is used, no signal
    out = fresh_support_touches(df)
    assert out.iloc[4] == 99.0 and math.isnan(out.iloc[6])


def test_a_close_below_the_level_breaks_it_without_a_signal():
    df = bars(GREEN, (102, 102.5, 98, 99, 1e6), (99, 104, 98.8, 103, 1e6), (103, 106, 102, 105, 1e6),
              (105, 105.5, 97, 98, 1e6),             # closes below 99: support failed
              (98, 101, 97.5, 100.5, 1e6))
    out = fresh_support_touches(df)
    assert math.isnan(out.iloc[4])


def test_levels_older_than_the_age_limit_are_forgotten():
    rows = [GREEN, (102, 102.5, 98, 99, 1e6), (99, 104, 98.8, 103, 1e6)] + [(103, 106, 102, 105, 1e6)] * 5
    rows.append((105, 105.5, 98.5, 101, 1e6))
    assert math.isnan(fresh_support_touches(bars(*rows), max_age=5).iloc[-1])
    assert fresh_support_touches(bars(*rows), max_age=50).iloc[-1] == 99.0


def test_no_lookahead_future_bars_do_not_change_earlier_levels():
    rows = [GREEN, (102, 102.5, 98, 99, 1e6), (99, 104, 98.8, 103, 1e6), (103, 106, 102, 105, 1e6),
            (105, 105.5, 98.5, 101, 1e6)]
    early = fresh_support_touches(bars(*rows))
    later = fresh_support_touches(bars(*(rows + [(101, 101, 90, 91, 1e6), (91, 95, 90, 94, 1e6)])))
    pd.testing.assert_series_equal(early, later.iloc[:5], check_names=False)


def cand_row(**over):
    base = dict(open=105, high=105.5, low=98.5, close=101.0, volume=1e6, sma50=95.0, sma200=90.0, atr14=2.0,
                msnr_level=99.0)
    return {**base, **over}


def test_candidates_need_the_trend_and_set_the_stop_half_an_atr_below_the_level():
    rows = pd.DataFrame.from_dict({"AAA": cand_row(), "BBB": cand_row(close=89.0, msnr_level=88.0),
                                   "CCC": cand_row(sma50=89.0), "DDD": cand_row(msnr_level=float("nan"))},
                                  orient="index")
    (c,) = MalaysianSnR().candidates(pd.Timestamp("2025-03-03"), rows)
    assert c.symbol == "AAA" and c.stop == pytest.approx(98.0) and c.signal_close == 101.0
    assert "fresh support" in c.reason and c.facts["level"] == 99.0


def test_stop_too_far_is_skipped():
    rows = pd.DataFrame.from_dict({"AAA": cand_row(close=110.0)}, orient="index")  # stop 98: 10.9% away
    assert MalaysianSnR().candidates(pd.Timestamp("2025-03-03"), rows) == []


def test_registered_with_a_fixed_two_r_target():
    assert STRATEGIES["msnr"] is MalaysianSnR
    assert getattr(MalaysianSnR, "exit_policy", "fixed_target") == "fixed_target"
    df = MalaysianSnR().prepare(add_indicators(bars(*([GREEN] * 30))))
    assert "msnr_level" in df.columns
