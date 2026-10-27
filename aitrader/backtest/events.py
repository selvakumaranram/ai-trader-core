"""RBI monetary-policy announcement days: strategy D takes no new entries on them or the session before."""
from __future__ import annotations

import bisect
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

import yaml

RBI_FILE = Path(__file__).resolve().parent.parent / "data" / "rbi_policy_dates.yaml"


def load_rbi_dates(path: Optional[Path] = None) -> Tuple[List[date], Optional[date], Optional[date]]:
    """(sorted policy dates, covered_from, covered_to); a missing or empty file gives ([], None, None)."""
    p = Path(path) if path is not None else RBI_FILE
    if not p.exists():
        return [], None, None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    dates = sorted(entry["date"] for entry in (data.get("dates") or []))
    if not dates:
        return [], None, None
    return dates, data.get("covered_from"), data.get("covered_to")


def blocked_entry_days(policy_days: Iterable[date], trading_days: Sequence[date]) -> Set[date]:
    """Each policy day plus the last trading session before it."""
    ordered = sorted(trading_days)
    out: Set[date] = set()
    for day in policy_days:
        out.add(day)
        i = bisect.bisect_left(ordered, day)
        if i > 0:
            out.add(ordered[i - 1])
    return out
