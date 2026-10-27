from datetime import date

from aitrader.backtest.events import blocked_entry_days, load_rbi_dates


def test_blocked_days_include_day_before():
    trading = [date(2024, 2, 5), date(2024, 2, 6), date(2024, 2, 7), date(2024, 2, 8), date(2024, 2, 9)]
    assert blocked_entry_days([date(2024, 2, 8)], trading) == {date(2024, 2, 7), date(2024, 2, 8)}


def test_policy_day_on_a_holiday_still_blocks_previous_session():
    trading = [date(2024, 2, 7), date(2024, 2, 9)]
    assert blocked_entry_days([date(2024, 2, 8)], trading) == {date(2024, 2, 7), date(2024, 2, 8)}


def test_missing_rbi_file(tmp_path):
    assert load_rbi_dates(tmp_path / "none.yaml") == ([], None, None)


def test_rbi_file_parsed(tmp_path):
    p = tmp_path / "rbi.yaml"
    p.write_text("covered_from: 2024-01-01\ncovered_to: 2024-12-31\ndates:\n"
                 "  - {date: 2024-06-07, note: MPC, source: https://rbi.org.in/x}\n"
                 "  - {date: 2024-02-08, note: MPC, source: https://rbi.org.in/y}\n", encoding="utf-8")
    assert load_rbi_dates(p) == ([date(2024, 2, 8), date(2024, 6, 7)], date(2024, 1, 1), date(2024, 12, 31))


def test_shipped_rbi_file_loads():
    dates, covered_from, covered_to = load_rbi_dates()
    assert dates == sorted(dates) and (covered_from is None) == (covered_to is None)
