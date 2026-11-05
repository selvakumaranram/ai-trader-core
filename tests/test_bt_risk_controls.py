import math

import pandas as pd
import pytest

from aitrader.backtest.costs import CostModel
from aitrader.backtest.engine import simulate
from aitrader.backtest.rules import DrawdownGuard, atr_trail_stop, round_trip_estimate, week_end_days
from aitrader.backtest.types import LONG, Candidate, MoneyRules
from test_bt_engine import FLAT, Scripted, prep, run

COSTS = CostModel()
NEW = MoneyRules(capital=100_000, risk_mode="percent", risk_pct=0.01, daily_loss_pct=0.03, max_positions=5,
                 max_position_value=25_000)


def run_regime(frames, plan_by_index, regime_by_index, money):
    days = list(frames[next(iter(frames))].index)
    plan = {days[i]: [Candidate(sym, LONG, days[i].date(), close, stop, 1.0, f"test {sym}", {})
                      for sym, close, stop in cands] for i, cands in plan_by_index.items()}
    regime = {days[i].date(): on for i, on in regime_by_index.items()}
    return simulate(frames, days, Scripted(plan), money, COSTS, regime=regime), days


# --- regime filter ---------------------------------------------------------------------------------------------

def test_regime_filter_blocks_long_entries_on_a_below_200_sma_signal_day():
    res, _ = run_regime({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 95)]}, {0: False},
                        MoneyRules(regime_filter=True))
    assert res.trades == [] and res.skips == {"regime_off": 1}


def test_regime_filter_allows_entries_when_nifty_is_above_its_average():
    res, _ = run_regime({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 95)]}, {0: True},
                        MoneyRules(regime_filter=True))
    assert len(res.trades) == 1 and res.trades[0].facts["regime_on"] is True


def test_regime_off_does_not_close_open_positions():
    res, _ = run_regime({"AAA": prep([FLAT] * 5)}, {0: [("AAA", 100, 95)]}, {0: True, 1: False, 2: False},
                        MoneyRules(regime_filter=True))
    assert [t.exit_reason for t in res.trades] == ["end_of_test"]


def test_regime_filter_off_ignores_the_regime():
    res, _ = run_regime({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 95)]}, {0: False}, MoneyRules())
    assert len(res.trades) == 1 and res.trades[0].facts["regime_on"] is False


# --- % of equity risk ------------------------------------------------------------------------------------------

def test_percent_risk_sizes_from_equity_at_the_previous_close():
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 95, 1.0)]}, money=NEW)
    (t,) = res.trades
    assert t.shares == math.floor(1_000 / 5.05)  # 1% of ₹1,00,000, entry 100.05, stop 95
    assert t.facts["risk_amount"] == pytest.approx(1_000) and t.facts["size_cut"] is None


def test_percent_risk_follows_equity_after_a_loss():
    rows = [FLAT, (100, 101, 94, 95, 1e6)] + [FLAT] * 4
    res, _ = run({"AAA": prep(rows), "BBB": prep([FLAT] * 6)},
                 {0: [("AAA", 100, 95, 1.0)], 2: [("BBB", 100, 95, 1.0)]}, money=NEW)
    first, second = res.trades
    equity_after = 100_000 + first.pnl
    assert second.facts["risk_amount"] == pytest.approx(0.01 * equity_after, abs=0.01)  # stored to the paisa
    assert second.shares == math.floor(0.01 * equity_after / 5.05)


def test_size_cut_by_position_cap_is_recorded():
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 99, 1.0)]}, money=NEW)
    (t,) = res.trades
    assert t.shares == math.floor(25_000 / 100.05) and t.facts["size_cut"] == "cap"
    assert t.facts["intended_shares"] == math.floor(1_000 / 1.05)


def test_daily_loss_stop_as_percent_of_equity():
    rows = [FLAT, (100, 101, 94, 95, 1e6), FLAT, FLAT]
    money = MoneyRules(capital=100_000, risk_mode="percent", risk_pct=0.01, daily_loss_pct=0.005,
                       max_position_value=25_000)
    res, _ = run({"AAA": prep(rows), "BBB": prep([FLAT] * 4)},
                 {0: [("AAA", 100, 95, 1.0)], 1: [("BBB", 100, 95, 1.0)]}, money=money)
    assert [t.symbol for t in res.trades] == ["AAA"] and res.skips == {"daily_loss_pause": 1}


# --- charge-aware skip -----------------------------------------------------------------------------------------

def test_round_trip_estimate_is_buy_and_sell_charges_plus_slippage_both_sides():
    shares, fill, open_ = 100, 100.05, 100.0
    sell_value = shares * open_ * (1 - COSTS.slippage)
    expected = (COSTS.buy_charges(shares * fill) + COSTS.sell_charges(sell_value)
                + shares * open_ * COSTS.slippage * 2)
    assert round_trip_estimate(COSTS, shares, fill, open_) == pytest.approx(expected)


@pytest.mark.parametrize("stop,skipped", [(99.5, True), (90, False)])
def test_charge_skip_threshold(stop, skipped):
    money = MoneyRules(capital=100_000, risk_mode="percent", risk_pct=0.01, max_position_value=25_000,
                       max_charge_to_risk=0.05)
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, stop, 1.0)]}, money=money)
    if skipped:
        assert res.trades == [] and res.skips == {"charges_too_high": 1}
    else:
        (t,) = res.trades
        est = round_trip_estimate(COSTS, t.shares, t.entry_price, 100.0)
        assert est / (t.shares * (t.entry_price - stop)) <= 0.05


# --- drawdown pause --------------------------------------------------------------------------------------------

def _days(n):
    return [d.date() for d in pd.bdate_range("2024-01-01", periods=n)]


def test_drawdown_guard_pauses_at_the_limit_and_resets_peak_on_resume():
    g = DrawdownGuard(0.15, 10, 100.0)
    days = _days(30)
    g.update(0, days[0], 110.0, True)
    g.update(1, days[1], 94.0, True)  # 94 / 110 = −14.5%: not yet
    assert not g.paused
    g.update(2, days[2], 93.5, True)  # −15.0%
    assert g.paused and g.log[0]["start_date"] == days[2].isoformat()
    for i in range(3, 12):  # fewer than 10 trading days since the pause began
        g.update(i, days[i], 90.0, True)
        assert g.paused
    g.update(12, days[12], 90.0, False)  # 10 days passed but NIFTY below its average
    assert g.paused
    g.update(13, days[13], 89.0, True)
    assert not g.paused and g.peak == 89.0
    assert g.log[0] == {"start_date": days[2].isoformat(), "resume_date": days[13].isoformat(),
                        "equity_at_pause": 93.5, "equity_at_resume": 89.0, "days_paused": 11}
    g.update(14, days[14], 80.0, True)  # −10.1% from the new peak: keeps trading
    assert not g.paused


def test_drawdown_pause_blocks_new_entries_in_the_engine():
    crash = [FLAT, (100, 101, 60, 60, 1e6)] + [(60, 61, 59, 60, 1e6)] * 4
    money = MoneyRules(capital=10_000, risk_per_trade=4_000, max_position_value=10_000, daily_loss_limit=1e9,
                       max_drawdown_pause=0.15)
    res, days = run({"AAA": prep(crash), "BBB": prep([FLAT] * 6)},
                    {0: [("AAA", 100, 50, 1.0)], 2: [("BBB", 100, 95, 1.0)]}, money=money)
    assert res.skips == {"drawdown_pause": 1}
    assert res.pauses and res.pauses[0]["start_date"] == days[1].date().isoformat()
    assert res.pauses[0]["resume_date"] is None


# --- A2 building blocks ----------------------------------------------------------------------------------------

def test_trailing_stop_never_moves_down():
    stop = 90.0
    for highest, atr in [(100, 2), (98, 5), (110, 3), (105, 1), (108, 10)]:
        new = atr_trail_stop(stop, highest, atr)
        assert new >= stop and new == max(stop, highest - 3 * atr)
        stop = new


def test_week_end_days_handle_holidays():
    # Mon 3 – Thu 6 Nov 2025 trade (Fri 7 is a holiday in this calendar); then Mon 10 – Fri 14, Mon 17 last
    days = [pd.Timestamp(d) for d in ("2025-11-03", "2025-11-04", "2025-11-05", "2025-11-06",
                                      "2025-11-10", "2025-11-11", "2025-11-12", "2025-11-13", "2025-11-14",
                                      "2025-11-17")]
    assert week_end_days(days) == {pd.Timestamp("2025-11-06"), pd.Timestamp("2025-11-14"),
                                   pd.Timestamp("2025-11-17")}


# --- charge check v2: 0.15 default, actual entry + final shares, ratios recorded ---------------------------------

def test_new_runs_default_to_a_fifteen_percent_charge_check_and_old_value_can_be_passed():
    from dataclasses import replace
    from aitrader.backtest.types import NEW_RUN_MONEY
    assert NEW_RUN_MONEY.max_charge_to_risk == 0.15
    assert replace(NEW_RUN_MONEY, max_charge_to_risk=0.05).max_charge_to_risk == 0.05


def test_charge_check_uses_actual_entry_and_final_shares_and_records_the_ratio():
    # cash limits the position to 60 of the 99 shares the risk would buy: the ratio must use 60 shares at the fill
    money = MoneyRules(capital=6_100, risk_mode="percent", risk_pct=0.163, max_position_value=25_000,
                       max_charge_to_risk=0.15)
    res, _ = run({"AAA": prep([FLAT, (101, 102, 100, 101, 1e6), FLAT, FLAT])}, {0: [("AAA", 100, 90, 1.0)]},
                 money=money)
    (t,) = res.trades
    assert t.shares < t.facts["intended_shares"] and t.facts["size_cut"] == "cash"
    expected = round_trip_estimate(COSTS, t.shares, t.entry_price, 101.0) / (t.shares * (t.entry_price - 90))
    assert t.facts["charge_to_risk"] == pytest.approx(expected, rel=1e-3)
    assert t.facts["entry_open"] == 101.0 and t.facts["signal_close"] == 100.0


def test_skipped_charge_ratios_are_collected():
    money = MoneyRules(capital=100_000, risk_mode="percent", risk_pct=0.01, max_position_value=25_000,
                       max_charge_to_risk=0.15)
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 99.8, 1.0)]}, money=money)
    assert res.skips == {"charges_too_high": 1}
    assert len(res.charge_ratios_skipped) == 1 and res.charge_ratios_skipped[0] > 0.15


def test_charge_ratio_recorded_even_with_the_check_off():
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].facts["charge_to_risk"] > 0 and res.charge_ratios_skipped == []


# --- re-entry cooldown, MFE, volatility at entry -----------------------------------------------------------------

STOPPED = (100, 101, 94, 95, 1e6)   # entry-day low touches the stop at 95
TARGET = (100, 111, 99, 110, 1e6)    # entry-day high reaches the 2R target (110.15)


def _aaa_after(first_bar, n=16):
    return prep([FLAT, first_bar] + [FLAT] * (n - 2))


@pytest.mark.parametrize("signal_index,blocked", [(5, True), (10, True), (11, False)])
def test_cooldown_blocks_reentry_for_ten_trading_days_after_a_stop(signal_index, blocked):
    money = MoneyRules(reentry_cooldown_days=10)
    res, days = run({"AAA": _aaa_after(STOPPED)}, {0: [("AAA", 100, 95, 1.0)],
                                                   signal_index: [("AAA", 100, 95, 1.0)]}, money=money)
    assert res.trades[0].exit_reason == "stop" and res.trades[0].exit_date == days[1].date()
    if blocked:  # entry would be at signal_index + 1, i.e. ≤ 10 trading days after the stop on day 1
        assert len(res.trades) == 1 and res.skips == {"cooldown_after_stop": 1}
    else:
        assert len(res.trades) == 2 and res.trades[1].entry_date == days[signal_index + 1].date()


def test_no_cooldown_after_a_target_exit():
    res, _ = run({"AAA": _aaa_after(TARGET)}, {0: [("AAA", 100, 95, 1.0)], 2: [("AAA", 100, 95, 1.0)]},
                 money=MoneyRules(reentry_cooldown_days=10))
    assert [t.exit_reason for t in res.trades][0] == "target" and len(res.trades) == 2 and res.skips == {}


def test_no_cooldown_after_a_time_exit():
    res, days = run({"AAA": prep([FLAT] * 30)}, {0: [("AAA", 100, 95, 1.0)], 22: [("AAA", 100, 95, 1.0)]},
                    money=MoneyRules(reentry_cooldown_days=10))
    assert res.trades[0].exit_reason == "time" and len(res.trades) == 2 and res.skips == {}


def test_cooldown_off_by_default_and_strategy_default_applies():
    res, _ = run({"AAA": _aaa_after(STOPPED)}, {0: [("AAA", 100, 95, 1.0)], 3: [("AAA", 100, 95, 1.0)]})
    assert len(res.trades) == 2  # MoneyRules() has no cooldown and the scripted strategy sets none
    days = list(_aaa_after(STOPPED).index)

    class Cooling(Scripted):
        PARAMS = {"reentry_cooldown_days": 10}

    plan = {days[0]: [Candidate("AAA", LONG, days[0].date(), 100, 95, 1.0, "t", {})],
            days[3]: [Candidate("AAA", LONG, days[3].date(), 100, 95, 1.0, "t", {})]}
    res = simulate({"AAA": _aaa_after(STOPPED)}, days, Cooling(plan), MoneyRules(), COSTS)
    assert len(res.trades) == 1 and res.skips == {"cooldown_after_stop": 1}
    res = simulate({"AAA": _aaa_after(STOPPED)}, days, Cooling(plan), MoneyRules(reentry_cooldown_days=0), COSTS)
    assert len(res.trades) == 2  # an explicit 0 in the money settings switches the strategy default off


def test_mfe_uses_only_prices_between_entry_and_exit():
    rows = [FLAT, (100, 103, 99, 102, 1e6), (102, 108, 101, 104, 1e6), (104, 105, 94, 95, 1e6), (95, 150, 94, 96, 1e6)]
    res, _ = run({"AAA": prep(rows)}, {0: [("AAA", 100, 95, 1.0)]})
    (t,) = res.trades
    assert t.exit_reason == "stop"
    assert t.facts["mfe_r"] == pytest.approx((108 - t.entry_price) / (t.entry_price - 95), abs=1e-4)  # not 150 (stored to 4 dp)


def test_mfe_excludes_the_exit_day_high_when_exiting_at_the_open():
    rows = [FLAT, (100, 103, 99, 102, 1e6), (90, 140, 89, 91, 1e6), FLAT]  # gaps below the stop: exit at the open
    (t,) = run({"AAA": prep(rows)}, {0: [("AAA", 100, 95, 1.0)]})[0].trades
    assert t.exit_reason == "stop_gap"
    assert t.facts["mfe_r"] == pytest.approx((103 - t.entry_price) / (t.entry_price - 95), abs=1e-4)


def test_volatility_at_entry_is_the_signal_day_atr_over_close():
    df = prep([FLAT] * 16 + [(100, 130, 70, 100, 1e6)])  # the entry day's huge range must not count
    days = list(df.index)
    res = simulate({"AAA": df}, days, Scripted({days[15]: [Candidate("AAA", LONG, days[15].date(), 100, 95, 1.0,
                                                                       "t", {})]}), MoneyRules(), COSTS)
    (t,) = res.trades
    assert t.facts["atr_pct"] == pytest.approx(float(df["atr14"].iloc[15]) / 100)



# --- short-term regime filter, drawdown risk scaling --------------------------------------------------------------

def test_risk_scaler_uses_a_never_resetting_peak_halves_below_ten_percent_restores_within_five():
    from aitrader.backtest.rules import RiskScaler
    r = RiskScaler()
    for equity, factor in [(100, 1.0), (110, 1.0), (99.1, 1.0), (98.9, 0.5), (103, 0.5), (104.4, 0.5),
                           (104.5, 1.0), (120, 1.0), (107.9, 0.5)]:
        r.update(equity)
        assert r.factor == factor, (equity, r.peak)
    assert r.peak == 120


def test_short_term_regime_blocks_a_long_entry_above_200_but_below_50_day_average():
    days = list(prep([FLAT] * 4).index)
    plan = {days[0]: [Candidate("AAA", LONG, days[0].date(), 100, 95, 1.0, "t", {})]}
    regime = {days[0].date(): True}
    regime50 = {days[0].date(): False}
    res = simulate({"AAA": prep([FLAT] * 4)}, days, Scripted(plan),
                   MoneyRules(regime_filter=True, regime_filter_short=True), COSTS, regime=regime, regime50=regime50)
    assert res.trades == [] and res.skips == {"short_regime_off": 1}
    res = simulate({"AAA": prep([FLAT] * 4)}, days, Scripted(plan), MoneyRules(regime_filter=True), COSTS,
                   regime=regime, regime50=regime50)
    (t,) = res.trades  # the short-term filter is off unless asked for
    assert t.facts["nifty_above_sma50"] is False


def test_dd_risk_scaling_halves_risk_after_a_ten_percent_fall_and_counts_the_days():
    crash = [FLAT, (100, 101, 60, 60, 1e6)] + [(60, 61, 59, 60, 1e6)] * 4
    money = MoneyRules(capital=10_000, risk_mode="percent", risk_pct=0.2, max_position_value=10_000,
                       daily_loss_limit=1e9, dd_risk_scaling=True)  # big risk so the crash is a >10% fall
    res, days = run({"AAA": prep(crash), "BBB": prep([FLAT] * 6)},
                    {0: [("AAA", 100, 50, 1.0)], 2: [("BBB", 100, 95, 1.0)]}, money=money)
    first, second = res.trades
    equity_before = res.equity[2].equity  # entry on day 3 sizes from day 2's close
    assert equity_before < 9_000 and second.facts["risk_amount"] == pytest.approx(0.1 * equity_before, abs=0.01)  # half of 20%
    assert res.half_risk_days == 4  # days 2–5 start below −10% (the crash closes on day 1)



# --- group / sector limit ----------------------------------------------------------------------------------------

def _group_run(money, groups, sectors, symbols):
    frames = {s: prep([FLAT] * 4) for s in symbols}
    days = list(frames[symbols[0]].index)
    plan = {days[0]: [Candidate(s, LONG, days[0].date(), 100, 95, 10.0 - k, "t", {}) for k, s in enumerate(symbols)]}
    return simulate(frames, days, Scripted(plan), money, COSTS, groups=groups, sectors=sectors)


def test_group_limit_blocks_a_second_adani_stock():
    groups = {"ADANIENT": "Adani", "ADANIPOWER": "Adani"}
    res = _group_run(MoneyRules(max_per_group=1, max_positions=5, capital=100_000),
                     groups, {}, ["ADANIENT", "ADANIPOWER", "TCS"])
    assert sorted(t.symbol for t in res.trades) == ["ADANIENT", "TCS"]
    assert res.skips == {"group_sector_limit": 1}
    assert next(t for t in res.trades if t.symbol == "ADANIENT").facts["group"] == "Adani"


def test_sector_limit_blocks_a_third_stock_from_the_same_sector():
    sectors = {"HDFCBANK": "Financial Services", "ICICIBANK": "Financial Services", "SBIN": "Financial Services",
               "TCS": "Information Technology"}
    res = _group_run(MoneyRules(max_per_group=1, max_per_sector=2, max_positions=5, capital=100_000),
                     {}, sectors, ["HDFCBANK", "ICICIBANK", "SBIN", "TCS"])
    assert sorted(t.symbol for t in res.trades) == ["HDFCBANK", "ICICIBANK", "TCS"]
    assert res.skips == {"group_sector_limit": 1}
    assert res.trades[0].facts["sector"] == "Financial Services"


def test_group_and_sector_files_cover_the_universe():
    from aitrader.backtest.groups import load_groups, load_sectors
    groups, sectors = load_groups(), load_sectors()
    assert {groups[s] for s in ("ADANIENT", "ADANIGREEN", "ADANIPOWER", "ADANIENSOL", "ATGL")} == {"Adani"}
    assert groups["TCS"] == "Tata" and groups["BAJFINANCE"] == "Bajaj" and groups["JIOFIN"] == "Reliance"
    assert groups["GRASIM"] == "Aditya Birla" and groups["IRFC"] == "PSU railways" and groups["PFC"] == "PSU power finance"
    assert len(sectors) >= 200 and sectors["HDFCBANK"] == "Financial Services"
