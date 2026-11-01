"""Strategies A2 (medium-term momentum) and B2 (pullback with a volatility-based stop)."""
import numpy as np
import pandas as pd
import pytest

from aitrader.backtest.costs import CostModel
from aitrader.backtest.engine import simulate
from aitrader.backtest.indicators import add_indicators
from aitrader.backtest.strategies import STRATEGIES
from aitrader.backtest.strategies.momentum_a2 import MomentumA2
from aitrader.backtest.strategies.pullback_b2 import PullbackB2
from aitrader.backtest.types import LONG, Candidate, MoneyRules
from bt_helpers import frame
from test_bt_engine import FLAT, prep
from test_bt_strategies import DAY, pull_row, rows

COSTS = CostModel()


# --- A2: scoring ----------------------------------------------------------------------------------------------

def walk(seed, n=300, drift=0.001):
    rng = np.random.default_rng(seed)
    closes = 100 * np.cumprod(1 + rng.normal(drift, 0.015, n))
    return [(c * 0.999, c * 1.01, c * 0.99, c, 2_000_000) for c in closes]


def test_a2_returns_skip_the_latest_month():
    df = MomentumA2().prepare(add_indicators(frame(walk(1))))
    c = df["close"]
    t = len(df) - 1
    assert df["r6"].iloc[t] == pytest.approx(c.iloc[t - 21] / c.iloc[t - 126] - 1)
    assert df["r12"].iloc[t] == pytest.approx(c.iloc[t - 21] / c.iloc[t - 252] - 1)
    std = c.pct_change().iloc[t - 125:t + 1].std()
    assert df["r6_voladj"].iloc[t] == pytest.approx(df["r6"].iloc[t] / std)


def test_a2_score_never_uses_later_bars():
    base = walk(2)
    later = base[:280] + [(o * 3, h * 3, low * 3, c * 3, v) for o, h, low, c, v in base[280:]]
    a = MomentumA2().prepare(add_indicators(frame(base)))
    b = MomentumA2().prepare(add_indicators(frame(later)))
    cols = ["r6", "r12", "r6_voladj", "value50"]
    pd.testing.assert_frame_equal(a[cols].iloc[:280], b[cols].iloc[:280])


def test_a2_score_ignores_the_last_twenty_days_of_returns():
    base = walk(3)
    bumped = base[:-20] + [(o * 1.5, h * 1.5, low * 1.5, c * 1.5, v) for o, h, low, c, v in base[-20:]]
    a = MomentumA2().prepare(add_indicators(frame(base)))
    b = MomentumA2().prepare(add_indicators(frame(bumped)))
    assert a["r6"].iloc[-1] == pytest.approx(b["r6"].iloc[-1]) and a["r12"].iloc[-1] == pytest.approx(b["r12"].iloc[-1])


def a2_row(**over):
    base = dict(close=110.0, sma50=100.0, sma200=90.0, atr14=2.0, bars=300, value50=2e8, r6=0.2, r12=0.3,
                r6_voladj=10.0)
    return {**base, **over}


def test_a2_eligibility_and_stop():
    r = rows(AAA=a2_row(), BBB=a2_row(bars=259), CCC=a2_row(close=95.0), DDD=a2_row(close=99.0, sma50=100.0,
                                                                                         sma200=80.0),
             EEE=a2_row(value50=9e7))
    out = MomentumA2().candidates(DAY, r)
    assert [c.symbol for c in out] == ["AAA"]
    assert out[0].stop == pytest.approx(110 - 2.5 * 2) and out[0].direction == LONG


def test_a2_weighted_percentile_score_and_top_ten():
    data = {f"S{i:02d}": a2_row(r6=i / 100, r12=(30 - i) / 100, r6_voladj=float(i)) for i in range(30)}
    out = MomentumA2().candidates(DAY, rows(**data))
    assert len(out) == 10
    df = rows(**data)
    expected = (50 * df["r6"].rank(pct=True) + 30 * df["r12"].rank(pct=True) + 20 * df["r6_voladj"].rank(pct=True))
    best = expected.sort_values(ascending=False, kind="mergesort").index[:10]
    assert [c.symbol for c in out] == list(best)
    assert out[0].score == pytest.approx(expected[best[0]])


def test_a2_rank_keep_is_top_thirty_eligible():
    data = {f"S{i:02d}": a2_row(r6=i / 100, r12=i / 100, r6_voladj=float(i)) for i in range(35)}
    data["S34"] = a2_row(close=85.0, r6=1.0, r12=1.0, r6_voladj=99.0)  # below its 200-day average
    keep = MomentumA2().rank_keep(DAY, rows(**data))
    assert len(keep) == 30 and "S34" not in keep and "S00" not in keep and "S33" in keep


# --- A2 through the engine -------------------------------------------------------------------------------------

class WeeklyScripted:
    key = name = "weekly"
    exit_policy, signal_frequency = "atr_trail", "weekly"
    PARAMS = {"time_stop_days": 60, "trail_atr": 3.0}

    def __init__(self, plan, keep=None):
        self.plan, self.keep, self.calls = plan, keep or {}, []

    def candidates(self, day, rows_):
        self.calls.append(day)
        return list(self.plan.get(day, []))

    def rank_keep(self, day, rows_):
        return self.keep.get(day, {"AAA"})  # short test series are never "eligible": keep unless told otherwise


def a2_frames(rows_):
    df = add_indicators(frame(rows_))
    return {"AAA": df}, list(df.index)


def cand(day, close=100.0, stop=90.0):
    return Candidate("AAA", LONG, day.date(), close, stop, 1.0, "test", {})


def test_weekly_strategy_is_asked_only_on_week_ends():
    frames, days = a2_frames([FLAT] * 12)  # Mon 1 Jan 2024 … Tue 16 Jan
    s = WeeklyScripted({})
    simulate(frames, days, s, MoneyRules(), COSTS)
    assert [d.day_name() for d in s.calls] == ["Friday", "Friday"]


def test_rank_exit_sells_at_next_open():
    frames, days = a2_frames([FLAT] * 15)
    friday1, friday2 = days[4], days[9]
    s = WeeklyScripted({friday1: [cand(friday1)]}, keep={friday2: set()})
    (t,) = simulate(frames, days, s, MoneyRules(), COSTS).trades
    assert t.exit_reason == "rank_exit" and t.exit_date == days[10].date()
    assert t.exit_price == pytest.approx(100 * (1 - COSTS.slippage))


def test_rank_exit_through_the_stop_is_a_stop_gap():
    rows_ = [FLAT] * 10 + [(80, 81, 79, 80, 1e6)] + [FLAT] * 4
    frames, days = a2_frames(rows_)
    friday1, friday2 = days[4], days[9]
    s = WeeklyScripted({friday1: [cand(friday1)]}, keep={friday2: set()})
    (t,) = simulate(frames, days, s, MoneyRules(), COSTS).trades
    assert t.exit_reason == "stop_gap" and t.exit_date == days[10].date()


def test_trailing_stop_rises_with_closes_and_exits_as_trail_stop():
    up = [(100 + k, 101 + k, 99 + k, 100 + k, 1e6) for k in range(20)]
    rows_ = up + [(118, 118, 100, 101, 1e6)] + [FLAT] * 3
    frames, days = a2_frames(rows_)
    friday1 = days[4]
    s = WeeklyScripted({friday1: [cand(friday1, close=104.0, stop=95.0)]})
    (t,) = simulate(frames, days, s, MoneyRules(max_position_value=20_000), COSTS).trades
    assert t.exit_reason == "trail_stop" and t.exit_date == days[20].date() and t.stop == 95.0


# --- B2 ---------------------------------------------------------------------------------------------------------

def b2(nifty_ret126=0.05):
    s = PullbackB2()
    s.nifty_ret126 = {DAY.date(): nifty_ret126}
    return s


def b2_row(**over):
    return pull_row(**{"atr14": 2.0, "ret126": 0.10, **over})


def test_b2_stop_is_never_closer_than_one_and_a_half_atr():
    (c,) = b2().candidates(DAY, rows(AAA=b2_row(low3=100.5)))  # 3-day low only 0.5% below
    assert c.stop == pytest.approx(101.0 - 1.5 * 2.0)
    (c,) = b2().candidates(DAY, rows(AAA=b2_row(low3=95.0)))  # 3-day low already further away
    assert c.stop == pytest.approx(95.0)


def test_b2_has_no_fixed_stop_band():
    assert len(b2().candidates(DAY, rows(AAA=b2_row(low3=85.0)))) == 1  # 16% away: B would skip it


def test_b2_relative_strength_filter_counts_the_skip():
    s = b2(nifty_ret126=0.10)
    assert s.candidates(DAY, rows(AAA=b2_row(ret126=0.10))) == []  # not stronger than NIFTY
    assert s.skips == {"weaker_than_nifty": 1}
    assert len(b2(nifty_ret126=0.09).candidates(DAY, rows(AAA=b2_row(ret126=0.10)))) == 1


def test_b2_relative_strength_uses_only_data_up_to_the_signal_day():
    base = walk(4, n=200)
    later = base[:150] + [(o * 2, h * 2, low * 2, c * 2, v) for o, h, low, c, v in base[150:]]
    a = PullbackB2().prepare(add_indicators(frame(base)))
    b = PullbackB2().prepare(add_indicators(frame(later)))
    pd.testing.assert_series_equal(a["ret126"].iloc[:150], b["ret126"].iloc[:150])
    c = a["close"]
    assert a["ret126"].iloc[149] == pytest.approx(c.iloc[149] / c.iloc[149 - 126] - 1)


@pytest.mark.parametrize("fill,skipped", [(99.9, True), (100.1, False), (103.9, False), (104.1, True)])
def test_b2_stop_distance_checked_against_the_actual_entry(fill, skipped):
    c = Candidate("AAA", LONG, DAY.date(), 101.0, 98.0, 1.0, "t", {"atr14": 2.0})  # stop 98, ATR 2: 100–104 ok
    reason = PullbackB2().entry_check(c, fill)
    assert (reason == "stop_distance_at_entry") is skipped


def test_b2_gap_down_open_leaving_under_one_atr_is_skipped_in_the_engine():
    df = prep([FLAT, (99.0, 100, 98.5, 99.5, 1e6), FLAT])  # opens at 99: entry − stop ≈ 1.05 < ATR 2
    days = list(df.index)

    class One(PullbackB2):
        def candidates(self, day, rows_):
            return [Candidate("AAA", LONG, days[0].date(), 100.0, 98.0, 1.0, "t", {"atr14": 2.0})] \
                if day == days[0] else []

    res = simulate({"AAA": df}, days, One(), MoneyRules(), COSTS)
    assert res.trades == [] and res.skips == {"stop_distance_at_entry": 1}


def test_new_strategies_are_registered():
    assert STRATEGIES["momentum_a2"] is MomentumA2 and STRATEGIES["pullback_b2"] is PullbackB2
