from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from typing import List, Tuple

from sqlalchemy.engine import Engine

from aitrader.db import repo

UNIVERSE_INDEX = "NIFTY 200"
INDICES = (("NIFTY 50", "^NSEI"), ("NIFTY BANK", "^NSEBANK"))
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def parse_constituents_csv(text: str) -> List[Tuple[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise ValueError("CSV is empty")
    columns = {name.strip().lower(): name for name in reader.fieldnames}
    symbol_col, name_col = columns.get("symbol"), columns.get("company name")
    if symbol_col is None:
        raise ValueError(f"CSV has no 'Symbol' column (found: {reader.fieldnames})")
    result, bad = [], []
    for row in reader:
        symbol = (row.get(symbol_col) or "").strip().upper()
        if not symbol:
            continue
        if not _SYMBOL_RE.match(symbol):
            bad.append(symbol)
            continue
        result.append((symbol, (row.get(name_col) or "").strip() if name_col else ""))
    if bad:
        raise ValueError(f"CSV contains invalid symbols: {bad[:5]}")
    if not result:
        raise ValueError("CSV contains no symbols")
    return result


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year - years, day=28)


def seed_universe(engine: Engine, csv_text: str, *, as_of: date, history_years: int = 10) -> dict:
    constituents = parse_constituents_csv(csv_text)
    assumed_from = _years_before(as_of, history_years)
    day_before = as_of - timedelta(days=1)
    with engine.begin() as conn:
        for symbol, yf_symbol in INDICES:
            repo.upsert_instrument(conn, kind="index", exchange="NSE", symbol=symbol, name=symbol,
                                   yf_symbol=yf_symbol)
        for symbol, name in constituents:
            iid = repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol=symbol, name=name or symbol,
                                         yf_symbol=f"{symbol}.NS")
            if not repo.has_membership(conn, UNIVERSE_INDEX, iid):
                # Membership before the first seed is unknown: record it as an explicit assumption.
                repo.add_membership(conn, index_name=UNIVERSE_INDEX, instrument_id=iid, valid_from=assumed_from,
                                    valid_to=day_before, assumption=True)
            if not repo.has_open_membership(conn, UNIVERSE_INDEX, iid):
                repo.add_membership(conn, index_name=UNIVERSE_INDEX, instrument_id=iid, valid_from=as_of,
                                    valid_to=None, assumption=False)
        removed = repo.deactivate_stocks_except(conn, "NSE", [s for s, _ in constituents])
        repo.close_open_memberships(conn, UNIVERSE_INDEX, removed, day_before)
    return {"stocks": len(constituents), "indices": len(INDICES), "deactivated": len(removed)}
