import numpy as np
import pytest

from aitrader.backtest.costs import CostModel
from aitrader.backtest.engine import simulate
from aitrader.backtest.indicators import add_indicators
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.types import LONG, Candidate, MoneyRules
from bt_helpers import frame

FLAT = (100, 101, 99, 100, 1e6)
COSTS = CostModel()


class Scripted:
    key = name = "scripted"

    def __init__(self, plan):
        self.plan = plan

    def candidates(self, day, rows):
        return list(self.plan.get(day, []))


def prep(rows):
    return add_indicators(frame(rows))


def run(frames, plan_by_index, money=MoneyRules(), spine=None):
    days = list(frames[spine or next(iter(frames))].index)
    plan = {days[i]: [Candidate(sym, LONG, days[i].date(), close, stop, score, f"test {sym}", {})
                      for sym, close, stop, score in cands]
            for i, cands in plan_by_index.items()}
    return simulate(frames, days, Scripted(plan), money, COSTS), days


def expected_pnl(shares, entry, exit_raw):
    buy = shares * entry
    sell = shares * exit_raw * (1 - COSTS.slippage)
    return sell - COSTS.sell_charges(sell) - buy - COSTS.buy_charges(buy)


def test_target_hit_next_day_with_costs_and_equity():
    res, days = run({"AAA": prep([FLAT, (100, 102, 99, 101, 1e6), (101, 111, 100, 110, 1e6), FLAT])},
                    {0: [("AAA", 100, 95, 1.0)]})
    (t,) = res.trades
    assert (t.entry_date, t.exit_date, t.shares, t.exit_reason) == (days[1].date(), days[2].date(), 69, "target")
    assert t.entry_price == pytest.approx(100.05) and t.target == pytest.approx(110.15)
    assert t.exit_price == pytest.approx(110.15 * 0.9995)
    assert t.pnl == pytest.approx(expected_pnl(69, 100.05, 110.15))
    assert t.r_multiple == pytest.approx(t.pnl / (69 * 5.05))
    assert len(res.equity) == 4 and res.equity[-1].equity == pytest.approx(20_000 + t.pnl)


def test_stop_and_target_same_day_counts_as_loss():
    res, _ = run({"AAA": prep([FLAT, FLAT, (101, 111, 94, 100, 1e6), FLAT])}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "stop_same_day" and res.trades[0].exit_price == pytest.approx(95 * 0.9995)


def test_gap_below_stop_after_entry_exits_at_open():
    res, _ = run({"AAA": prep([FLAT, FLAT, (90, 91, 89, 90, 1e6), FLAT])}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "stop_gap" and res.trades[0].exit_price == pytest.approx(90 * 0.9995)


@pytest.mark.parametrize("open_,reason", [(94, "gap_through_stop"), (111, "gap_past_target")])
def test_entry_gaps_are_skipped(open_, reason):
    res, _ = run({"AAA": prep([FLAT, (open_, open_ + 1, open_ - 1, open_, 1e6), FLAT])}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades == [] and res.skips == {reason: 1}


def test_risk_cap_sizes_down():
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 90, 1.0)]})
    assert res.trades[0].shares == 39


def test_share_above_position_cap_is_size_zero():
    res, _ = run({"MRF": prep([(8000, 8010, 7990, 8000, 1e6)] * 3)}, {0: [("MRF", 8000, 7900, 1.0)]})
    assert res.trades == [] and res.skips == {"size_zero": 1}


def test_not_enough_cash():
    res, _ = run({"AAA": prep([FLAT] * 3)}, {0: [("AAA", 100, 95, 1.0)]}, money=MoneyRules(capital=50))
    assert res.trades == [] and res.skips == {"no_cash": 1}


def test_slots_go_to_the_best_score():
    res, _ = run({"AAA": prep([FLAT] * 3), "BBB": prep([FLAT] * 3)},
                 {0: [("BBB", 100, 95, 1.0), ("AAA", 100, 95, 2.0)]}, money=MoneyRules(max_positions=1))
    assert [t.symbol for t in res.trades] == ["AAA"] and res.skips == {"no_slot": 1}


def test_already_held_is_skipped():
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 95, 1.0)], 1: [("AAA", 100, 95, 1.0)]})
    assert len(res.trades) == 1 and res.skips == {"already_held": 1}


def test_daily_loss_pause_blocks_next_day_entries():
    res, _ = run({"AAA": prep([FLAT, (100, 101, 94, 95, 1e6), FLAT, FLAT]), "BBB": prep([FLAT] * 4)},
                 {0: [("AAA", 100, 95, 1.0)], 1: [("BBB", 100, 95, 1.0)]},
                 money=MoneyRules(daily_loss_limit=300))
    assert [t.exit_reason for t in res.trades] == ["stop"] and res.skips == {"daily_loss_pause": 1}


def test_time_stop_after_twenty_days():
    res, days = run({"AAA": prep([FLAT] * 25)}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "time" and res.trades[0].exit_date == days[21].date()


def test_end_of_test_closes_open_positions():
    res, days = run({"AAA": prep([FLAT] * 5)}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "end_of_test" and res.trades[0].exit_date == days[4].date()


def test_missing_bar_waits_for_next_bar():
    full = frame([FLAT] * 6)
    frames = {"SPN": add_indicators(full), "AAA": add_indicators(full.drop(full.index[2]))}
    res, days = run(frames, {0: [("AAA", 100, 95, 1.0)]}, spine="SPN")
    assert res.notes == {"missing_bar_days": 1}
    assert res.trades[0].exit_reason == "end_of_test" and res.trades[0].exit_date == days[5].date()


def walk(rng, n=320):
    closes = 1000 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    return [(c * 0.998, c * 1.01, c * 0.99, c, 1_000_000) for c in closes]


def test_no_lookahead_changing_future_bars_changes_no_earlier_decision():
    rng = np.random.default_rng(7)
    raw = {s: frame(walk(rng)) for s in ("AAA", "BBB", "CCC")}
    days = list(raw["AAA"].index)
    cut = days[280]

    def sim(frames):
        return simulate({s: add_indicators(f) for s, f in frames.items()}, days, Momentum(), MoneyRules(), COSTS)

    changed = {}
    for s, f in raw.items():
        g = f.copy()
        g.loc[g.index > cut, ["open", "high", "low", "close"]] *= 1.5
        changed[s] = g
    base, other = sim(raw), sim(changed)

    def entries(res):
        return sorted((t.symbol, t.signal_date, t.entry_date, t.entry_price, t.shares, t.stop)
                      for t in res.trades if t.entry_date <= cut.date())

    assert entries(base) and entries(base) == entries(other)
    upto = [p for p in base.equity if p.day <= cut.date()]
    assert upto == other.equity[:len(upto)]


def test_leftover_cash_crumb_position_is_skipped():
    # 7,500 capital: AAA takes 69 shares (≈ ₹6,912); the ≈ ₹588 left would buy only 5 of BBB's intended 69 shares
    res, _ = run({"AAA": prep([FLAT] * 3), "BBB": prep([FLAT] * 3)},
                 {0: [("AAA", 100, 95, 2.0), ("BBB", 100, 95, 1.0)]}, money=MoneyRules(capital=7500))
    assert [t.symbol for t in res.trades] == ["AAA"] and res.skips == {"no_cash": 1}


def test_held_trade_closes_at_last_good_close_on_a_flagged_jump():
    frames = {"AAA": prep([FLAT, FLAT, FLAT, (25, 25.3, 24.7, 25, 4e6), (25, 25.3, 24.7, 25, 4e6)])}
    days = list(frames["AAA"].index)
    plan = {days[0]: [Candidate("AAA", LONG, days[0].date(), 100, 95, 1.0, "t", {})]}
    res = simulate(frames, days, Scripted(plan), MoneyRules(), COSTS, data_flags={"AAA": {days[3].date()}})
    (t,) = res.trades
    assert (t.exit_reason, t.exit_date) == ("data_problem", days[3].date())
    assert t.exit_price == pytest.approx(100 * 0.9995)


@pytest.mark.parametrize("flag_days_before_entry,skipped", [(0, True), (31, True), (400, False)])
def test_entries_near_a_flagged_jump_are_skipped(flag_days_before_entry, skipped):
    from datetime import timedelta
    frames = {"AAA": prep([FLAT] * 4)}
    days = list(frames["AAA"].index)
    flag = days[1].date() - timedelta(days=flag_days_before_entry)
    plan = {days[0]: [Candidate("AAA", LONG, days[0].date(), 100, 95, 1.0, "t", {})]}
    res = simulate(frames, days, Scripted(plan), MoneyRules(), COSTS, data_flags={"AAA": {flag}})
    assert (res.skips == {"data_problem": 1}) is skipped and (len(res.trades) == 0) is skipped


class ScriptedD(Scripted):
    exit_policy = "partial_trail"
    PARAMS = {"partial_r": 1.5, "time_stop_days": 30, "min_stop": 0.01, "max_stop": 0.08}


def run_d(frames, plan_by_index, blocked=None, money=MoneyRules()):
    days = list(frames[next(iter(frames))].index)
    plan = {days[i]: [Candidate(sym, LONG, days[i].date(), close, stop, 1.0, f"test {sym}", {})
                      for sym, close, stop in cands] for i, cands in plan_by_index.items()}
    return simulate(frames, days, ScriptedD(plan), money, COSTS, blocked_entry_days=blocked), days


def test_partial_then_breakeven_exit():
    rows = [FLAT, (100, 101, 99, 100, 1e6), (101, 108, 100.5, 107, 1e6), (106, 106.5, 99.9, 100.2, 1e6), FLAT]
    res, days = run_d({"AAA": prep(rows)}, {0: [("AAA", 100, 95)]})
    (t,) = res.trades
    fill, level = 100.05, 100.05 + 1.5 * 5.05
    p1, p2 = level * 0.9995, fill * 0.9995
    assert (t.shares, t.exit_reason, t.exit_date) == (69, "trail_stop", days[3].date())
    assert t.facts["partial"] == {"date": days[2].date().isoformat(), "price": pytest.approx(p1), "shares": 34}
    assert t.exit_price == pytest.approx((34 * p1 + 35 * p2) / 69)
    assert t.charges == pytest.approx(COSTS.buy_charges(69 * fill) + COSTS.sell_charges(34 * p1)
                                      + COSTS.sell_charges(35 * p2))
    assert t.pnl == pytest.approx(34 * p1 + 35 * p2 - 69 * fill - t.charges)
    assert t.r_multiple == pytest.approx(t.pnl / (69 * 5.05))


def test_partial_then_trail_then_time():
    rows = [FLAT, (100, 101, 99, 100, 1e6), (101, 108, 100.5, 107, 1e6)]
    rows += [(108 + 0.1 * k, 110 + 0.1 * k, 107 + 0.1 * k, 109 + 0.1 * k, 1e6) for k in range(40)]
    frame_ = prep(rows)
    frame_["low5"] = frame_["low"].rolling(5).min()
    res, days = run_d({"AAA": frame_}, {0: [("AAA", 100, 95)]})
    (t,) = res.trades
    assert (t.exit_reason, t.exit_date) == ("time", days[31].date())


def test_one_share_partial_closes_everything():
    rows = [(6000, 6010, 5990, 6000, 1e6), (6000, 6010, 5990, 6000, 1e6), (6100, 6400, 6050, 6300, 1e6),
            (6300, 6310, 6290, 6300, 1e6)]
    res, _ = run_d({"BIG": prep(rows)}, {0: [("BIG", 6000, 5800)]})
    (t,) = res.trades
    assert (t.shares, t.exit_reason) == (1, "partial") and t.facts["partial"]["shares"] == 1


def test_stop_distance_rechecked_on_fill():
    res, _ = run_d({"AAA": prep([FLAT, (104, 105, 103, 104, 1e6), FLAT])}, {0: [("AAA", 100, 95)]})
    assert res.trades == [] and res.skips == {"stop_distance": 1}


def test_event_day_blocks_entry():
    frame_ = prep([FLAT] * 4)
    days = list(frame_.index)
    res, _ = run_d({"AAA": frame_}, {0: [("AAA", 100, 95)]}, blocked={days[1].date()})
    assert res.trades == [] and res.skips == {"event_day": 1}
