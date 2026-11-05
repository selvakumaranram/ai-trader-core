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


def test_trailing_stop_uses_closes_since_entry_not_the_entry_price():
    # Gap-up entry at 110 that closes at 106: the trail must start from the 106 close, not the 110 fill.
    rows_ = [FLAT] * 20 + [(110, 111, 105, 106, 1e6), (106, 107, 101, 106, 1e6)] + [(106, 107, 105, 106, 1e6)] * 2
    frames, days = a2_frames(rows_)
    friday1 = days[19]  # 20 warm-up days so ATR(14) exists
    assert friday1.day_name() == "Friday"
    s = WeeklyScripted({friday1: [cand(friday1, close=100.0, stop=90.0)]})
    (t,) = simulate(frames, days, s, MoneyRules(max_position_value=20_000), COSTS).trades
    assert t.exit_reason == "end_of_test"  # low 101 stays above 106 − 3 × ATR ≈ 98; 110 − 3 × ATR ≈ 102 would not


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


# --- C2 ---------------------------------------------------------------------------------------------------------

from aitrader.backtest.strategies.breakout_c2 import BreakoutC2  # noqa: E402


def brk_row(**over):
    base = dict(open=104.0, high=106.0, low=103.5, close=105.0, volume=3e6, vol20_prior=1e6, high20_prior=103.0,
                low20_prior=95.0, sma50=98.0, atr14=2.0)
    return {**base, **over}


def test_c2_keeps_c_entry_but_drops_the_eight_percent_stop_cap():
    from aitrader.backtest.strategies.breakout import Breakout
    wide = brk_row(close=125.0, high=126.0, atr14=2.0)  # stop 101 is 19% below the close: C refuses, C2 keeps it
    assert Breakout().candidates(DAY, rows(AAA=wide)) == []
    (c,) = BreakoutC2().candidates(DAY, rows(AAA=wide))
    assert c.stop == pytest.approx(103.0 - 2.0) and c.score == pytest.approx(3.0)
    assert BreakoutC2().candidates(DAY, rows(AAA=brk_row(volume=1.4e6))) == []  # volume rule kept


@pytest.mark.parametrize("fill,skipped", [(102.9, True), (103.1, False), (106.9, False), (107.1, True)])
def test_c2_stop_distance_checked_at_the_actual_entry(fill, skipped):
    c = Candidate("AAA", LONG, DAY.date(), 105.0, 101.0, 3.0, "t", {"atr14": 2.0})  # 1–3 ATR = 103–107
    assert (BreakoutC2().entry_check(c, fill) == "stop_distance_at_entry") is skipped


def test_c2_skips_a_gap_up_that_puts_the_stop_more_than_three_atr_away():
    df = prep([FLAT] * 20 + [(110, 111, 109, 110, 1e6), FLAT])  # signal close 100, stop 98, ATR ≈ 2, opens 110
    days = list(df.index)

    class One(BreakoutC2):
        def candidates(self, day, rows_):
            return [Candidate("AAA", LONG, day.date(), 100.0, 98.0, 3.0, "t", {"atr14": 2.0})] \
                if day == days[19] else []

    res = simulate({"AAA": df}, days, One(), MoneyRules(), COSTS)
    assert res.trades == [] and res.skips == {"stop_distance_at_entry": 1}


def test_c2_trails_with_no_target_and_a_forty_day_time_stop():
    df = prep([FLAT] * 20 + [(100.5, 101, 100, 100.5, 1e6)] * 45)  # quiet: stop never reached
    days = list(df.index)

    class One(BreakoutC2):
        def candidates(self, day, rows_):
            return [Candidate("AAA", LONG, day.date(), 100.0, 97.5, 3.0, "t", {"atr14": 2.0})] \
                if day == days[19] else []

    (t,) = simulate({"AAA": df}, days, One(), MoneyRules(), COSTS).trades
    assert t.exit_reason == "time" and t.exit_date == days[20 + 40].date() and t.target == float("inf")
    assert BreakoutC2.exit_policy == "atr_trail" and BreakoutC2.PARAMS["trail_atr"] == 3.0
    assert STRATEGIES["breakout_c2"] is BreakoutC2


def test_index_tables_use_the_family_rules():
    from aitrader.backtest.runner import index_strategy_for
    from aitrader.backtest.strategies.breakout import Breakout
    from aitrader.backtest.strategies.pullback import Pullback
    for key in ("breakout", "breakout_c2"):
        s = index_strategy_for(key)
        assert type(s) is Breakout and s.use_volume is False
    assert type(index_strategy_for("pullback_b2")) is Pullback and type(index_strategy_for("pullback")) is Pullback


# --- B3 ---------------------------------------------------------------------------------------------------------

def test_b3_is_b2_entries_with_a_trailing_exit_forty_day_stop_and_cooldown():
    from aitrader.backtest.strategies.pullback_b3 import PullbackB3
    assert issubclass(PullbackB3, PullbackB2) and STRATEGIES["pullback_b3"] is PullbackB3
    assert PullbackB3.exit_policy == "atr_trail" and PullbackB3.PARAMS["trail_atr"] == 3.0
    assert PullbackB3.PARAMS["time_stop_days"] == 40 and PullbackB3.PARAMS["reentry_cooldown_days"] == 10
    s = PullbackB3()
    s.nifty_ret126 = {DAY.date(): 0.05}
    (c,) = s.candidates(DAY, rows(AAA=b2_row(low3=100.5)))
    assert c.stop == pytest.approx(101.0 - 1.5 * 2.0)  # B2's 1.5-ATR minimum stop kept


def test_b3_has_no_target_exit():
    from aitrader.backtest.strategies.pullback_b3 import PullbackB3
    up = [FLAT] * 20 + [(100 + 2 * k, 101 + 2 * k, 99 + 2 * k, 100 + 2 * k, 1e6) for k in range(1, 20)]
    df = prep(up)
    days = list(df.index)

    class One(PullbackB3):
        def candidates(self, day, rows_):
            return [Candidate("AAA", LONG, day.date(), 100.0, 97.0, 1.0, "t", {"atr14": 2.0})] \
                if day == days[19] else []

    (t,) = simulate({"AAA": df}, days, One(), MoneyRules(), COSTS).trades
    assert t.exit_reason == "end_of_test" and t.target == float("inf") and t.r_multiple > 2  # ran past 2R



# --- C3 ---------------------------------------------------------------------------------------------------------

def test_c3_stop_is_never_closer_than_one_and_a_half_atr_below_the_signal_close():
    from aitrader.backtest.strategies.breakout_c3 import BreakoutC3
    (c,) = BreakoutC3().candidates(DAY, rows(AAA=brk_row(close=104.0, high=104.5, atr14=2.0)))  # level 103
    assert c.stop == pytest.approx(min(103.0 - 2.0, 104.0 - 3.0))  # 101: the 1.5-ATR floor wins
    (c,) = BreakoutC3().candidates(DAY, rows(AAA=brk_row(close=108.0, high=108.5, atr14=2.0)))
    assert c.stop == pytest.approx(101.0)  # the breakout-level stop is already further away
    for close in (103.5, 104.0, 106.0, 110.0):
        (c,) = BreakoutC3().candidates(DAY, rows(AAA=brk_row(close=close, high=close + 0.5)))
        assert close - c.stop >= 1.5 * 2.0 - 1e-9


def test_c3_settings_and_registry():
    from aitrader.backtest.strategies.breakout_c3 import BreakoutC3
    from aitrader.backtest.strategies.breakout_c2 import BreakoutC2
    assert issubclass(BreakoutC3, BreakoutC2) and STRATEGIES["breakout_c3"] is BreakoutC3
    p = BreakoutC3.PARAMS
    assert (p["stop_floor_atr"], p["regime_filter_short"], p["reentry_cooldown_days"], p["time_stop_days"],
            p["trail_atr"], p["max_stop"]) == (1.5, True, 10, 40, 3.0, None)
    from aitrader.backtest.runner import index_strategy_for
    from aitrader.backtest.strategies.breakout import Breakout
    assert type(index_strategy_for("breakout_c3")) is Breakout



# --- A3 options: time stop off, rank exit off, weekly signal weekday ------------------------------------------

def test_time_stop_off_never_produces_a_time_exit():
    frames, days = a2_frames([FLAT] * 90)
    s = WeeklyScripted({days[4]: [cand(days[4])]})
    s.PARAMS = {**s.PARAMS, "time_stop_days": None}
    (t,) = simulate(frames, days, s, MoneyRules(), COSTS).trades
    assert t.exit_reason == "end_of_test"


def test_a2_rank_off_exits_only_below_the_200_day_average():
    a2 = MomentumA2()
    a2.PARAMS = {**MomentumA2.PARAMS, "keep_top": None}
    today = rows(AAA=a2_row(close=85.0), BBB=a2_row(r6=-0.5, r12=-0.5, r6_voladj=-9.0), CCC=a2_row())
    eligible = today.loc[["BBB", "CCC"]]
    assert a2.weekly_exits(DAY, eligible, today, {"AAA", "BBB"}) == {"AAA": "below_sma200"}
    base = MomentumA2()  # keep_top 30: the same holdings go out as rank exits when not in the kept set
    assert base.weekly_exits(DAY, eligible, today, {"AAA"}) == {"AAA": "rank_exit"}


def test_weekly_exit_reason_reaches_the_trade_and_rank_off_never_says_rank_exit():
    frames, days = a2_frames([FLAT] * 15)

    class BelowAverage(WeeklyScripted):
        def weekly_exits(self, day, eligible, today, held):
            return {"AAA": "below_sma200"} if day == days[9] else {}

    s = BelowAverage({days[4]: [cand(days[4])]})
    (t,) = simulate(frames, days, s, MoneyRules(), COSTS).trades
    assert t.exit_reason == "below_sma200" and t.exit_date == days[10].date()


def test_wednesday_signal_days_fall_back_to_the_last_trading_day_before_it():
    from aitrader.backtest.rules import week_signal_days
    days = [pd.Timestamp(d) for d in ("2025-11-03", "2025-11-04", "2025-11-05", "2025-11-06", "2025-11-07",  # normal
                                      "2025-11-10", "2025-11-11", "2025-11-13", "2025-11-14",  # Wed 12 holiday
                                      "2025-11-20", "2025-11-21")]  # Mon–Wed holidays: no Wednesday-or-before
    assert week_signal_days(days, 2) == {pd.Timestamp("2025-11-05"), pd.Timestamp("2025-11-11")}
    assert week_signal_days(days, None) == {pd.Timestamp("2025-11-07"), pd.Timestamp("2025-11-14"),
                                            pd.Timestamp("2025-11-21")}


def test_a3_is_a2_with_the_a3_best_settings():
    from aitrader.backtest.strategies.momentum_a3 import MomentumA3
    assert issubclass(MomentumA3, MomentumA2) and STRATEGIES["momentum_a3"] is MomentumA3
    assert MomentumA3.PARAMS["time_stop_days"] is None and MomentumA3.PARAMS["keep_top"] == 50
    assert {k: v for k, v in MomentumA3.PARAMS.items() if k not in ("time_stop_days", "keep_top")} == \
        {k: v for k, v in MomentumA2.PARAMS.items() if k not in ("time_stop_days", "keep_top")}



# --- C3 improvements: confirmation entry, A+ volume filter -------------------------------------------------------

class Confirming(WeeklyScripted):
    exit_policy, signal_frequency = "atr_trail", "daily"
    PARAMS = {"time_stop_days": 40, "trail_atr": 3.0, "entry_confirm": True}

    def weekly_exits(self, day, eligible, today, held):
        return {}


def confirm_frames(next_bar):
    df = add_indicators(frame([FLAT] * 20 + [(100, 102, 99, 101, 1e6), next_bar] + [FLAT] * 3))
    return {"AAA": df}, list(df.index)


def confirm_cand(days):
    return Candidate("AAA", LONG, days[20].date(), 101.0, 96.0, 1.0, "t", {"signal_high": 102.0, "atr14": 2.0})


def test_confirmation_entry_buys_only_when_the_next_day_trades_above_the_signal_high():
    frames, days = confirm_frames((101, 101.8, 100, 101, 1e6))  # never above 102
    s = Confirming({days[20]: [confirm_cand(days)]})
    res = simulate(frames, days, s, MoneyRules(), COSTS)
    assert res.trades == [] and res.skips == {"not_confirmed": 1}


def test_confirmation_entry_fills_at_the_signal_high_or_the_open_if_higher():
    frames, days = confirm_frames((101, 103, 100.5, 102.5, 1e6))
    (t,) = simulate(frames, days, Confirming({days[20]: [confirm_cand(days)]}), MoneyRules(), COSTS).trades
    assert t.entry_price == pytest.approx(102.0 * (1 + COSTS.slippage))
    frames, days = confirm_frames((103, 104, 102.8, 103.5, 1e6))  # gaps above the signal high
    (t,) = simulate(frames, days, Confirming({days[20]: [confirm_cand(days)]}), MoneyRules(), COSTS).trades
    assert t.entry_price == pytest.approx(103.0 * (1 + COSTS.slippage))


def test_breakout_records_the_signal_high_and_a_plus_raises_the_volume_bar():
    from aitrader.backtest.strategies.breakout_c3 import BreakoutC3
    (c,) = BreakoutC3().candidates(DAY, rows(AAA=brk_row()))
    assert c.facts["signal_high"] == 106.0
    a_plus = BreakoutC3()
    a_plus.PARAMS = {**BreakoutC3.PARAMS, "volume_surge": 3.0}
    assert a_plus.candidates(DAY, rows(AAA=brk_row(volume=2.9e6))) == []
    assert len(a_plus.candidates(DAY, rows(AAA=brk_row(volume=3.1e6)))) == 1


# --- C4: breakout + momentum ------------------------------------------------------------------------------------

def test_c4_takes_a_breakout_only_when_the_stock_is_in_the_momentum_top_30():
    from aitrader.backtest.strategies.breakout_momentum import BreakoutMomentum
    data = {f"S{i:02d}": {**brk_row(volume=1e6), **a2_row(close=105.0, r6=i / 100, r12=i / 100, r6_voladj=float(i))}
            for i in range(40)}
    data["S39"] = {**data["S39"], "volume": 3e6}  # strongest momentum + a breakout
    data["S00"] = {**data["S00"], "volume": 3e6}  # a breakout, but the weakest momentum
    out = BreakoutMomentum().candidates(DAY, rows(**data))
    assert [c.symbol for c in out] == ["S39"]
    assert out[0].facts["momentum_rank"] == 1
    assert STRATEGIES["breakout_momentum"] is BreakoutMomentum


# --- Selling-pressure exits (C3 variants) ---------------------------------------------------------------------

class Pressure(WeeklyScripted):
    exit_policy, signal_frequency = "atr_trail", "daily"

    def __init__(self, plan, **params):
        super().__init__(plan)
        self.PARAMS = {"time_stop_days": 40, "trail_atr": 3.0, **params}

    def weekly_exits(self, day, eligible, today, held):
        return {}


def pressure_run(after_entry, **params):
    """Signal on day 20 (breakout level 100.5, stop 96), entry at day 21's open, then the given bars."""
    bars = [FLAT] * 20 + [(100, 102, 99, 101, 1e6), (101, 102, 100.6, 101.5, 1e6)] + after_entry + [FLAT] * 3
    df = add_indicators(frame(bars))
    days = list(df.index)
    cand = Candidate("AAA", LONG, days[20].date(), 101.0, 96.0, 1.0, "t", {"level": 100.5, "atr14": 2.0})
    return simulate({"AAA": df}, days, Pressure({days[20]: [cand]}, **params), MoneyRules(), COSTS), days


def test_failed_breakout_sells_at_the_next_open_after_a_close_back_below_the_breakout_level():
    after = [(101, 101.5, 99.5, 100.0, 1e6), (99.8, 100.5, 99, 100, 1e6)]
    res, days = pressure_run(after, failed_breakout_days=5)
    (t,) = res.trades
    assert t.exit_reason == "failed_breakout" and t.exit_date == days[23].date()
    assert t.exit_price == pytest.approx(99.8 * (1 - COSTS.slippage))


def test_failed_breakout_is_ignored_after_the_window_and_when_switched_off():
    after = [(101.5, 102, 101, 101.5, 1e6)] * 3 + [(101, 101.5, 99.5, 100.0, 1e6)]  # back below on day 4 held
    res, _ = pressure_run(after, failed_breakout_days=3)
    (t,) = res.trades
    assert t.exit_reason == "end_of_test"
    res, _ = pressure_run([(101, 101.5, 99.5, 100.0, 1e6)])
    (t,) = res.trades
    assert t.exit_reason == "end_of_test"


def test_distribution_sells_after_two_heavy_volume_down_days_within_ten_days():
    after = [(101.5, 102, 100.8, 101.0, 3e6),   # down day, 3x volume
             (101, 102, 100.8, 101.6, 1e6),     # up day
             (101.6, 102, 100.6, 100.8, 3e6),   # second heavy down day -> sell next open
             (100.7, 101, 100, 100.5, 1e6)]
    res, days = pressure_run(after, distribution_days=2)
    (t,) = res.trades
    assert t.exit_reason == "distribution" and t.exit_date == days[25].date()


def test_distribution_ignores_down_days_on_normal_volume():
    after = [(101.5, 102, 100.8, 101.0, 1.2e6), (101, 102, 100.8, 101.6, 1e6), (101.6, 102, 100.6, 100.8, 3e6)]
    res, _ = pressure_run(after, distribution_days=2)
    (t,) = res.trades
    assert t.exit_reason == "end_of_test"
