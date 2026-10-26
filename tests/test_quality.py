from datetime import date

from aitrader.models import Bar
from aitrader.quality import (
    BAR_CHECKS,
    check_jumps,
    check_missing,
    check_ohlc,
    check_source_mismatch,
    check_stale,
    find_unknown_closed_days,
)


def bar(day, o=100.0, h=101.0, low=99.0, c=100.5, v=1000):
    return Bar(date(2026, 10, day), o, h, low, c, v)


def test_ohlc_invalid_detects_each_problem():
    issues = check_ohlc([bar(1), bar(2, low=101.0), bar(5, h=99.5), bar(6, o=0.0), bar(7, v=-1)], instrument_id=7)
    assert [i.trade_date.day for i in issues] == [2, 5, 6, 7]
    assert all(i.check_name == "ohlc_invalid" and i.severity == "error" and i.instrument_id == 7 for i in issues)


def test_missing_bar_ignores_known_closed_days():
    expected = [date(2026, 10, d) for d in (1, 2, 5)]
    issues = check_missing([bar(1)], expected, closed_days={date(2026, 10, 2)})
    assert [(i.check_name, i.trade_date.day) for i in issues] == [("missing_bar", 5)]


def test_jump_flagged_unless_corporate_action_nearby():
    bars = [bar(1, c=100.0),
            bar(2, o=130.0, h=131.0, low=129.0, c=130.0),
            bar(5, o=130.0, h=131.0, low=129.0, c=130.5)]
    assert [i.trade_date.day for i in check_jumps(bars, [])] == [2]
    assert check_jumps(bars, [date(2026, 10, 2)]) == []


def test_stale_repeat_after_three_identical_days():
    bars = [bar(1), bar(2), bar(5), bar(6, c=101.0)]
    issues = check_stale(bars)
    assert [(i.check_name, i.trade_date.day) for i in issues] == [("stale_repeat", 5)]


def test_source_mismatch_over_half_percent():
    primary = [bar(1, c=100.0), bar(2, c=100.0)]
    secondary = [bar(1, c=100.4), bar(2, c=101.0)]
    issues = check_source_mismatch(primary, secondary)
    assert [(i.check_name, i.severity, i.trade_date.day) for i in issues] == [("source_mismatch", "info", 2)]


def test_unknown_closed_days_are_expected_days_with_no_bars_anywhere():
    expected = [date(2026, 10, d) for d in (1, 2, 5)]
    assert find_unknown_closed_days({date(2026, 10, 1), date(2026, 10, 5)}, expected) == [date(2026, 10, 2)]


def test_bar_checks_lists_every_bar_level_check():
    assert set(BAR_CHECKS) == {"missing_bar", "ohlc_invalid", "unexplained_jump", "stale_repeat",
                               "source_mismatch", "unknown_closed_day"}


def _jump_bars(next_open, next_close):
    from aitrader.models import Bar as B
    return [B(date(2024, 1, 1), 100, 101, 99, 100, 10), B(date(2024, 1, 2), next_open, next_open + 1,
                                                           next_close - 1, next_close, 10)]


def test_history_scan_classifies_unadjusted_bonus_vs_real_move():
    from aitrader.models import CorporateAction
    from aitrader.quality import scan_history_jumps
    (bonus,) = scan_history_jumps(_jump_bars(25, 25), [], 0.35)  # 4.00x overnight gap = unadjusted 3:1 bonus
    assert bonus.check_name == "history_adjustment" and "×4" in bonus.detail
    (crash,) = scan_history_jumps(_jump_bars(60, 58), [], 0.35)  # -40% gap: 1.67x is no split factor
    assert crash.check_name == "history_jump" and crash.severity == "warn"
    (late,) = scan_history_jumps(_jump_bars(100, 55), [], 0.35)  # crash after the open: never an adjustment
    assert late.check_name == "history_jump"
    assert scan_history_jumps(_jump_bars(98, 97), [], 0.35) == []
    matching = [CorporateAction(date(2024, 1, 2), "split", 1, 4)]
    assert scan_history_jumps(_jump_bars(25, 25), matching, 0.35) == []  # recorded and the ratio matches
    wrong_ratio = [CorporateAction(date(2024, 1, 2), "split", 1, 2)]
    assert [i.check_name for i in scan_history_jumps(_jump_bars(25, 25), wrong_ratio, 0.35)] == ["history_adjustment"]
