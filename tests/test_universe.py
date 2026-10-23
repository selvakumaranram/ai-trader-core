from datetime import date

import pytest
from sqlalchemy import select

from aitrader.db import repo
from aitrader.db.schema import index_membership
from aitrader.universe import parse_constituents_csv, seed_universe

CSV = "Company Name,Industry,Symbol,Series,ISIN Code\nInfosys Ltd.,IT,INFY,EQ,INE009A01021\nM&M Ltd.,Auto,M&M,EQ,X\n"


def test_parse_handles_bom_padding_and_lowercase_headers():
    text = "﻿ company name , industry , symbol \nInfosys Ltd., IT , infy \n"
    assert parse_constituents_csv(text) == [("INFY", "Infosys Ltd.")]


def test_parse_rejects_missing_symbol_column_and_bad_symbols():
    with pytest.raises(ValueError):
        parse_constituents_csv("Name\nInfosys\n")
    with pytest.raises(ValueError):
        parse_constituents_csv("Symbol\n<script>\n")
    with pytest.raises(ValueError):
        parse_constituents_csv("Symbol\n\n")


def test_seed_creates_indices_stocks_and_flagged_memberships(engine):
    counts = seed_universe(engine, CSV, as_of=date(2026, 10, 21))
    assert counts == {"stocks": 2, "indices": 2, "deactivated": 0}
    with engine.connect() as conn:
        symbols = {(i["kind"], i["symbol"], i["yf_symbol"]) for i in repo.active_instruments(conn)}
        rows = conn.execute(select(index_membership)).all()
    assert ("stock", "INFY", "INFY.NS") in symbols and ("index", "NIFTY BANK", "^NSEBANK") in symbols
    assumed = [r for r in rows if r.is_backfilled_assumption]
    current = [r for r in rows if not r.is_backfilled_assumption]
    assert len(assumed) == 2 and all(r.valid_from == date(2016, 10, 21) and r.valid_to == date(2026, 10, 20)
                                     for r in assumed)
    assert len(current) == 2 and all(r.valid_to is None for r in current)


def test_reseed_is_idempotent_and_deactivates_removed(engine):
    seed_universe(engine, CSV, as_of=date(2026, 10, 21))
    seed_universe(engine, CSV, as_of=date(2026, 10, 21))
    counts = seed_universe(engine, "Symbol\nINFY\n", as_of=date(2026, 11, 1))
    assert counts["deactivated"] == 1
    with engine.connect() as conn:
        assert "M&M" not in {i["symbol"] for i in repo.active_instruments(conn)}
        open_rows = conn.execute(select(index_membership).where(index_membership.c.valid_to.is_(None))).all()
    assert len(open_rows) == 1
