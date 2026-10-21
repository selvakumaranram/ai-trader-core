from __future__ import annotations

from datetime import date, timedelta
from typing import Collection, Iterable, List, Optional, Sequence, Set

from aitrader.models import Bar, Issue

BAR_CHECKS = ("missing_bar", "ohlc_invalid", "unexplained_jump", "stale_repeat",
              "source_mismatch", "unknown_closed_day")


def _ordered(bars: Iterable[Bar]) -> List[Bar]:
    return sorted(bars, key=lambda b: b.trade_date)


def check_ohlc(bars: Sequence[Bar], instrument_id: Optional[int] = None) -> List[Issue]:
    issues = []
    for b in _ordered(bars):
        problems = []
        if min(b.open, b.high, b.low, b.close) <= 0:
            problems.append("non-positive price")
        if b.low > min(b.open, b.close):
            problems.append("low above open/close")
        if b.high < max(b.open, b.close):
            problems.append("high below open/close")
        if b.volume < 0:
            problems.append("negative volume")
        if problems:
            issues.append(Issue("ohlc_invalid", "error", b.trade_date, "; ".join(problems), instrument_id))
    return issues


def check_missing(bars: Sequence[Bar], expected_days: Iterable[date],
                  closed_days: Collection[date] = frozenset(),
                  instrument_id: Optional[int] = None) -> List[Issue]:
    have = {b.trade_date for b in bars}
    return [
        Issue("missing_bar", "warn", d, "no bar on a trading day", instrument_id)
        for d in sorted(expected_days)
        if d not in have and d not in closed_days
    ]


def check_jumps(bars: Sequence[Bar], action_dates: Iterable[date], threshold: float = 0.20,
                instrument_id: Optional[int] = None) -> List[Issue]:
    near_action: Set[date] = set()
    for d in action_dates:
        near_action |= {d - timedelta(days=1), d, d + timedelta(days=1)}
    ordered = _ordered(bars)
    issues = []
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.trade_date in near_action or prev.close <= 0:
            continue
        for label, move in (("close-to-open", cur.open / prev.close - 1),
                            ("close-to-close", cur.close / prev.close - 1)):
            if abs(move) > threshold:
                issues.append(Issue("unexplained_jump", "warn", cur.trade_date,
                                    f"{label} move {move:+.1%} with no recorded split/bonus", instrument_id))
                break
    return issues


def check_stale(bars: Sequence[Bar], run_length: int = 3,
                instrument_id: Optional[int] = None) -> List[Issue]:
    ordered = _ordered(bars)
    issues, streak = [], 1
    for prev, cur in zip(ordered, ordered[1:]):
        same = (prev.open, prev.high, prev.low, prev.close, prev.volume) == (
            cur.open, cur.high, cur.low, cur.close, cur.volume)
        streak = streak + 1 if same else 1
        if streak == run_length:
            issues.append(Issue("stale_repeat", "warn", cur.trade_date,
                                f"identical OHLCV for {run_length} consecutive trading days", instrument_id))
    return issues


def check_source_mismatch(primary: Sequence[Bar], secondary: Sequence[Bar], tolerance: float = 0.005,
                          instrument_id: Optional[int] = None) -> List[Issue]:
    other = {b.trade_date: b for b in secondary}
    issues = []
    for b in _ordered(primary):
        o = other.get(b.trade_date)
        if o is None or b.close <= 0:
            continue
        diff = o.close / b.close - 1
        if abs(diff) > tolerance:
            issues.append(Issue("source_mismatch", "info", b.trade_date,
                                f"close differs by {diff:+.2%} between sources", instrument_id))
    return issues


def find_unknown_closed_days(days_with_any_bar: Collection[date], expected_days: Iterable[date]) -> List[date]:
    return sorted(d for d in expected_days if d not in days_with_any_bar)
