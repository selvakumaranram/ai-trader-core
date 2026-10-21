from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import resources
from typing import Iterable, List

import yaml


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def trading_days(self, start: date, end: date) -> List[date]:
        days, current = [], start
        while current <= end:
            if self.is_trading_day(current):
                days.append(current)
            current += timedelta(days=1)
        return days

    @classmethod
    def from_yaml_texts(cls, texts: Iterable[str]) -> "TradingCalendar":
        holidays = set()
        for text_ in texts:
            document = yaml.safe_load(text_) or {}
            for entry in document.get("holidays") or []:
                holidays.add(date.fromisoformat(str(entry["date"])))
        return cls(frozenset(holidays))

    @classmethod
    def bundled(cls) -> "TradingCalendar":
        files = resources.files("aitrader.data")
        texts = [
            f.read_text(encoding="utf-8")
            for f in files.iterdir()
            if f.name.startswith("nse_holidays_") and f.name.endswith(".yaml")
        ]
        return cls.from_yaml_texts(texts)
