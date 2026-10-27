import pytest

from aitrader.backtest.costs import CostModel
from aitrader.backtest.rules import exit_check, gap_skip, planned_target, size_position
from aitrader.backtest.types import LONG, SHORT, MoneyRules


def test_zerodha_delivery_charges_worked_example():
    costs = CostModel()
    assert costs.buy_charges(7000) == pytest.approx(7 + 0.2079 + 0.007 + 1.05 + 0.18 * (0.2079 + 0.007))
    assert costs.sell_charges(7700) == pytest.approx(7.7 + 0.22869 + 0.0077 + 0.18 * (0.22869 + 0.0077) + 15.93)


def test_planned_target_both_directions():
    assert planned_target(100, 95, 2) == 110
    assert planned_target(100, 105, 2) == 90


@pytest.mark.parametrize("direction,open_,expected", [
    (LONG, 94, "gap_through_stop"), (LONG, 111, "gap_past_target"), (LONG, 100, None),
    (SHORT, 106, "gap_through_stop"), (SHORT, 89, "gap_past_target"), (SHORT, 100, None),
])
def test_gap_skip(direction, open_, expected):
    stop, target = (95, 110) if direction == LONG else (105, 90)
    assert gap_skip(direction, open_, stop, target) == expected


def check(bar, entry_day=False, days_held=1, direction=LONG, stop=95, target=110):
    return exit_check(direction, bar, stop, target, entry_day=entry_day, days_held=days_held, time_stop=20)


def test_exit_rules_long():
    assert check((90, 92, 88, 91)) == (90, "stop_gap")
    assert check((112, 113, 111, 112)) == (112, "target_gap")
    assert check((100, 111, 94, 100)) == (95, "stop_same_day")
    assert check((100, 101, 94, 96)) == (95, "stop")
    assert check((100, 111, 99, 108)) == (110, "target")
    assert check((100, 101, 99, 100), days_held=20) == (100, "time")
    assert check((100, 101, 99, 100)) is None


def test_entry_day_ignores_gap_rules():
    assert check((90, 92, 88, 91), entry_day=True, days_held=0) == (95, "stop")


def test_exit_rules_short():
    assert check((106, 107, 104, 106), direction=SHORT, stop=105, target=90) == (106, "stop_gap")
    assert check((100, 106, 89, 100), direction=SHORT, stop=105, target=90) == (105, "stop_same_day")
    assert check((100, 101, 89, 95), direction=SHORT, stop=105, target=90) == (90, "target")


def test_size_position_caps():
    money = MoneyRules()
    assert size_position(100.05, 95, money) == 69   # ₹7,000 cap beats 79 by risk
    assert size_position(100.05, 90, money) == 39   # risk cap: 400 / 10.05
    assert size_position(8000, 7900, money) == 0    # one share above the position cap
    assert size_position(100, 100, money) == 0      # no risk distance


def ptc(bar, entry_day=False, days_held=1, partial_done=False, direction=LONG, stop=95, level=107.5):
    from aitrader.backtest.rules import partial_trail_check
    return partial_trail_check(direction, bar, stop=stop, partial_level=level, partial_done=partial_done,
                               entry_day=entry_day, days_held=days_held, time_stop=30)


def test_partial_trail_gaps():
    assert ptc((94, 96, 93, 95)) == ("exit", 94, "stop_gap")
    assert ptc((108, 109, 107.8, 108.5)) == ("partial", 108, "partial_gap")
    assert ptc((108, 109, 107.8, 108.5), entry_day=True, days_held=0) == ("partial", 107.5, "partial")
    assert ptc((94, 96, 93, 95), entry_day=True, days_held=0) == ("exit", 95, "stop")


def test_partial_trail_same_bar_is_a_loss():
    assert ptc((100, 108, 94, 100)) == ("exit", 95, "stop_same_day")


def test_partial_then_nothing_more_that_bar():
    assert ptc((100, 108, 99, 106)) == ("partial", 107.5, "partial")
    assert ptc((100, 108, 99, 106), partial_done=True, entry_day=True) is None  # same bar, original stop


def test_after_partial_stop_is_a_trail_stop_and_time_limit():
    assert ptc((101, 102, 99.5, 100), partial_done=True, stop=100) == ("exit", 100, "trail_stop")
    assert ptc((101, 102, 100.5, 101.5), partial_done=True, stop=100, days_held=30) == ("exit", 101.5, "time")


def test_partial_trail_short_mirror():
    assert ptc((100, 101, 92, 93), direction=SHORT, stop=105, level=92.5) == ("partial", 92.5, "partial")
    assert ptc((106, 107, 105.5, 106), direction=SHORT, stop=105, level=92.5) == ("exit", 106, "stop_gap")
    assert ptc((100, 106, 92, 100), direction=SHORT, stop=105, level=92.5) == ("exit", 105, "stop_same_day")


def test_next_trail_stop():
    from aitrader.backtest.rules import next_trail_stop
    assert next_trail_stop(LONG, 95, 100, False, 98, 110) == 95
    assert next_trail_stop(LONG, 95, 100, True, 98, 110) == 100
    assert next_trail_stop(LONG, 100, 100, True, 103, 110) == 103
    assert next_trail_stop(SHORT, 105, 100, True, 90, 97) == 97
    assert next_trail_stop(LONG, 100, 100, True, float("nan"), 110) == 100
