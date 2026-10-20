from datetime import date

from aitrader.calendar import TradingCalendar

YAML = """
year: 2026
holidays:
  - {date: 2026-01-26, name: Republic Day}
"""


def test_weekends_are_closed():
    cal = TradingCalendar()
    assert cal.is_trading_day(date(2026, 10, 23)) is True   # Friday
    assert cal.is_trading_day(date(2026, 10, 24)) is False  # Saturday
    assert cal.is_trading_day(date(2026, 10, 25)) is False  # Sunday


def test_holiday_from_yaml_is_closed():
    cal = TradingCalendar.from_yaml_texts([YAML])
    assert cal.is_trading_day(date(2026, 1, 26)) is False
    assert cal.is_trading_day(date(2026, 1, 27)) is True


def test_trading_days_is_inclusive_and_skips_closed_days():
    cal = TradingCalendar.from_yaml_texts([YAML])
    days = cal.trading_days(date(2026, 1, 23), date(2026, 1, 27))
    assert days == [date(2026, 1, 23), date(2026, 1, 27)]


def test_bundled_calendar_loads():
    assert isinstance(TradingCalendar.bundled(), TradingCalendar)
