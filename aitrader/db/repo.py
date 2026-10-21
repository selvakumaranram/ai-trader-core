from __future__ import annotations

from datetime import date
from typing import Collection, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import delete, func, literal_column, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from aitrader.db.schema import (
    corporate_actions,
    daily_bars,
    data_quality_issues,
    index_membership,
    ingestion_runs,
    instruments,
    option_contracts,
    option_snapshots,
)
from aitrader.models import Bar, CorporateAction, Issue, OptionContract

_BAR_VALUES = ("open", "high", "low", "close", "volume", "price_basis")


# --- instruments ------------------------------------------------------------

def upsert_instrument(conn: Connection, *, kind: str, exchange: str, symbol: str, name: str,
                      yf_symbol: Optional[str]) -> int:
    stmt = pg_insert(instruments).values(kind=kind, exchange=exchange, symbol=symbol, name=name,
                                         yf_symbol=yf_symbol, active=True)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_instruments_exchange_symbol",
        set_={"kind": stmt.excluded.kind, "name": stmt.excluded.name,
              "yf_symbol": stmt.excluded.yf_symbol, "active": True},
    ).returning(instruments.c.id)
    return conn.execute(stmt).scalar_one()


def deactivate_stocks_except(conn: Connection, exchange: str, keep_symbols: Collection[str]) -> List[int]:
    stmt = (update(instruments)
            .where(instruments.c.kind == "stock", instruments.c.exchange == exchange,
                   instruments.c.active.is_(True), instruments.c.symbol.not_in(list(keep_symbols)))
            .values(active=False).returning(instruments.c.id))
    return list(conn.execute(stmt).scalars())


def active_instruments(conn: Connection) -> List[dict]:
    rows = conn.execute(
        select(instruments.c.id, instruments.c.kind, instruments.c.exchange, instruments.c.symbol,
               instruments.c.yf_symbol, instruments.c.kite_token)
        .where(instruments.c.active.is_(True)).order_by(instruments.c.kind, instruments.c.symbol)
    ).mappings()
    return [dict(r) for r in rows]


def set_kite_tokens(conn: Connection, tokens: Dict[Tuple[str, str], int]) -> int:
    count = 0
    for (exchange, symbol), token in tokens.items():
        count += conn.execute(update(instruments)
                              .where(instruments.c.exchange == exchange, instruments.c.symbol == symbol)
                              .values(kite_token=token)).rowcount
    return count


# --- daily bars & corporate actions -------------------------------------------

def upsert_daily_bars(conn: Connection, instrument_id: int, bars: Sequence[Bar], *, source: str,
                      price_basis: str) -> Tuple[int, int]:
    latest = {b.trade_date: b for b in bars}  # one row per date; last one wins
    if not latest:
        return 0, 0
    rows = [dict(instrument_id=instrument_id, trade_date=b.trade_date, open=b.open, high=b.high, low=b.low,
                 close=b.close, volume=b.volume, source=source, price_basis=price_basis)
            for b in latest.values()]
    stmt = pg_insert(daily_bars).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[daily_bars.c.instrument_id, daily_bars.c.trade_date, daily_bars.c.source],
        set_={**{c: stmt.excluded[c] for c in _BAR_VALUES}, "ingested_at": func.now()},
        where=tuple_(*[daily_bars.c[c] for c in _BAR_VALUES]).is_distinct_from(
            tuple_(*[stmt.excluded[c] for c in _BAR_VALUES])),
    ).returning(literal_column("(xmax = 0)").label("inserted"))
    flags = list(conn.execute(stmt).scalars())
    inserted = sum(1 for f in flags if f)
    return inserted, len(flags) - inserted


def load_bars(conn: Connection, instrument_id: int, *, source: str, start: Optional[date] = None,
              end: Optional[date] = None) -> List[Bar]:
    q = select(daily_bars).where(daily_bars.c.instrument_id == instrument_id, daily_bars.c.source == source)
    if start:
        q = q.where(daily_bars.c.trade_date >= start)
    if end:
        q = q.where(daily_bars.c.trade_date <= end)
    return [Bar(r.trade_date, float(r.open), float(r.high), float(r.low), float(r.close), int(r.volume))
            for r in conn.execute(q.order_by(daily_bars.c.trade_date))]


def bar_date_range(conn: Connection, instrument_id: int, source: str) -> Tuple[Optional[date], Optional[date]]:
    row = conn.execute(select(func.min(daily_bars.c.trade_date), func.max(daily_bars.c.trade_date))
                       .where(daily_bars.c.instrument_id == instrument_id, daily_bars.c.source == source)).one()
    return row[0], row[1]


def price_basis_of(conn: Connection, instrument_id: int, source: str) -> Optional[str]:
    return conn.execute(select(daily_bars.c.price_basis)
                        .where(daily_bars.c.instrument_id == instrument_id, daily_bars.c.source == source)
                        .limit(1)).scalar()


def days_with_any_bar(conn: Connection, start: date, end: date, source: str) -> Set[date]:
    return set(conn.execute(select(daily_bars.c.trade_date).distinct()
                            .where(daily_bars.c.source == source,
                                   daily_bars.c.trade_date.between(start, end))).scalars())


def upsert_corporate_actions(conn: Connection, instrument_id: int, actions: Sequence[CorporateAction], *,
                             source: str) -> int:
    if not actions:
        return 0
    rows = [dict(instrument_id=instrument_id, ex_date=a.ex_date, kind=a.kind, source=source,
                 ratio_from=a.ratio_from, ratio_to=a.ratio_to) for a in actions]
    stmt = pg_insert(corporate_actions).values(rows).on_conflict_do_nothing()
    return len(conn.execute(stmt.returning(corporate_actions.c.instrument_id)).all())


def action_dates(conn: Connection, instrument_id: int) -> List[date]:
    return list(conn.execute(select(corporate_actions.c.ex_date).distinct()
                             .where(corporate_actions.c.instrument_id == instrument_id)
                             .order_by(corporate_actions.c.ex_date)).scalars())


# --- options -----------------------------------------------------------------

def upsert_option_contracts(conn: Connection, contracts: Sequence[OptionContract], *, seen_on: date) -> int:
    unique = {(c.tradingsymbol, c.expiry): c for c in contracts}
    if not unique:
        return 0
    rows = [dict(underlying=c.underlying, expiry=c.expiry, strike=c.strike, option_type=c.option_type,
                 lot_size=c.lot_size, tradingsymbol=c.tradingsymbol, kite_token=c.kite_token,
                 first_seen=seen_on, last_seen=seen_on) for c in unique.values()]
    stmt = pg_insert(option_contracts).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_option_contracts_symbol_expiry",
        set_={"last_seen": stmt.excluded.last_seen, "kite_token": stmt.excluded.kite_token,
              "lot_size": stmt.excluded.lot_size},
    )
    # psycopg 3 reports rowcount -1 for multi-row VALUES; count RETURNING rows instead.
    return len(conn.execute(stmt.returning(option_contracts.c.id)).all())


def live_option_contracts(conn: Connection, as_of: date) -> List[Tuple[int, OptionContract]]:
    rows = conn.execute(select(option_contracts).where(option_contracts.c.expiry >= as_of)
                        .order_by(option_contracts.c.expiry, option_contracts.c.strike,
                                  option_contracts.c.option_type))
    return [(r.id, OptionContract(r.underlying, r.expiry, float(r.strike), r.option_type, r.lot_size,
                                  r.tradingsymbol, r.kite_token)) for r in rows]


def insert_option_snapshots(conn: Connection, rows: Sequence[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(option_snapshots).values(list(rows)).on_conflict_do_nothing()
    return len(conn.execute(stmt.returning(option_snapshots.c.contract_id)).all())


# --- runs & issues -------------------------------------------------------------

def start_run(conn: Connection, job: str, run_date: date, source: Optional[str] = None) -> int:
    return conn.execute(ingestion_runs.insert().values(job=job, run_date=run_date, status="running",
                                                       source=source).returning(ingestion_runs.c.id)).scalar_one()


def finish_run(conn: Connection, run_id: int, status: str, counts: dict, message: str = "") -> None:
    conn.execute(update(ingestion_runs).where(ingestion_runs.c.id == run_id)
                 .values(status=status, counts=counts, message=message, finished_at=func.now()))


def last_run_date(conn: Connection, job: str, statuses: Collection[str] = ("ok", "partial")) -> Optional[date]:
    return conn.execute(select(func.max(ingestion_runs.c.run_date))
                        .where(ingestion_runs.c.job == job, ingestion_runs.c.status.in_(list(statuses)))).scalar()


def insert_issues(conn: Connection, run_id: int, issues: Sequence[Issue]) -> int:
    if not issues:
        return 0
    conn.execute(data_quality_issues.insert(), [
        dict(run_id=run_id, instrument_id=i.instrument_id, contract_id=i.contract_id, trade_date=i.trade_date,
             check_name=i.check_name, severity=i.severity, detail=i.detail) for i in issues])
    return len(issues)


def delete_issues(conn: Connection, check_names: Collection[str], start: date, end: date) -> int:
    return conn.execute(delete(data_quality_issues)
                        .where(data_quality_issues.c.check_name.in_(list(check_names)),
                               data_quality_issues.c.trade_date.between(start, end))).rowcount


def recent_runs(conn: Connection, limit: int) -> List[dict]:
    rows = conn.execute(select(ingestion_runs).order_by(ingestion_runs.c.id.desc()).limit(limit)).mappings()
    return [dict(r) for r in rows]


def recent_issues(conn: Connection, severity: Optional[str], limit: int) -> List[dict]:
    q = (select(data_quality_issues, instruments.c.symbol, option_contracts.c.tradingsymbol)
         .select_from(data_quality_issues
                      .outerjoin(instruments, instruments.c.id == data_quality_issues.c.instrument_id)
                      .outerjoin(option_contracts, option_contracts.c.id == data_quality_issues.c.contract_id))
         .order_by(data_quality_issues.c.trade_date.desc(), data_quality_issues.c.id.desc()).limit(limit))
    if severity:
        q = q.where(data_quality_issues.c.severity == severity)
    return [dict(r) for r in conn.execute(q).mappings()]


def coverage(conn: Connection) -> dict:
    bars = {}
    for kind, n, first, last in conn.execute(
            select(instruments.c.kind, func.count(func.distinct(daily_bars.c.instrument_id)),
                   func.min(daily_bars.c.trade_date), func.max(daily_bars.c.trade_date))
            .select_from(daily_bars.join(instruments, instruments.c.id == daily_bars.c.instrument_id))
            .group_by(instruments.c.kind)):
        bars[kind] = {"instruments": n, "first_date": first, "last_date": last}
    active = dict(conn.execute(select(instruments.c.kind, func.count())
                               .where(instruments.c.active.is_(True)).group_by(instruments.c.kind)).all())
    snaps = conn.execute(select(func.count(), func.min(option_snapshots.c.snapshot_at),
                                func.max(option_snapshots.c.snapshot_at))).one()
    return {"active_instruments": active, "bars": bars,
            "option_snapshots": {"rows": snaps[0], "first_at": snaps[1], "last_at": snaps[2]}}


def issue_counts(conn: Connection) -> Dict[str, int]:
    return dict(conn.execute(select(data_quality_issues.c.severity, func.count())
                             .group_by(data_quality_issues.c.severity)).all())


def last_run_per_job(conn: Connection) -> Dict[str, dict]:
    latest_ids = select(func.max(ingestion_runs.c.id)).group_by(ingestion_runs.c.job)
    rows = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id.in_(latest_ids))).mappings()
    return {r["job"]: dict(r) for r in rows}


# --- index membership ----------------------------------------------------------

def has_membership(conn: Connection, index_name: str, instrument_id: int) -> bool:
    return conn.execute(select(func.count()).select_from(index_membership)
                        .where(index_membership.c.index_name == index_name,
                               index_membership.c.instrument_id == instrument_id)).scalar_one() > 0


def has_open_membership(conn: Connection, index_name: str, instrument_id: int) -> bool:
    return conn.execute(select(func.count()).select_from(index_membership)
                        .where(index_membership.c.index_name == index_name,
                               index_membership.c.instrument_id == instrument_id,
                               index_membership.c.valid_to.is_(None))).scalar_one() > 0


def add_membership(conn: Connection, *, index_name: str, instrument_id: int, valid_from: date,
                   valid_to: Optional[date], assumption: bool) -> None:
    conn.execute(pg_insert(index_membership).values(
        index_name=index_name, instrument_id=instrument_id, valid_from=valid_from, valid_to=valid_to,
        is_backfilled_assumption=assumption).on_conflict_do_nothing())


def close_open_memberships(conn: Connection, index_name: str, instrument_ids: Collection[int],
                           valid_to: date) -> int:
    if not instrument_ids:
        return 0
    return conn.execute(update(index_membership)
                        .where(index_membership.c.index_name == index_name,
                               index_membership.c.instrument_id.in_(list(instrument_ids)),
                               index_membership.c.valid_to.is_(None))
                        .values(valid_to=valid_to)).rowcount


# --- freshness helpers ---------------------------------------------------------

def latest_bar_date(conn: Connection, source: str) -> Optional[date]:
    return conn.execute(select(func.max(daily_bars.c.trade_date)).where(daily_bars.c.source == source)).scalar()


def _snapshot_day():
    return func.date(func.timezone("Asia/Kolkata", option_snapshots.c.snapshot_at))


def first_snapshot_day(conn: Connection) -> Optional[date]:
    return conn.execute(select(func.min(_snapshot_day()))).scalar()


def snapshot_days(conn: Connection, start: date, end: date) -> Set[date]:
    day = _snapshot_day()
    return set(conn.execute(select(day).distinct().where(day.between(start, end))).scalars())


def issue_dates(conn: Connection, check_name: str, start: date, end: date) -> Set[date]:
    return set(conn.execute(select(data_quality_issues.c.trade_date).distinct()
                            .where(data_quality_issues.c.check_name == check_name,
                                   data_quality_issues.c.trade_date.between(start, end))).scalars())
