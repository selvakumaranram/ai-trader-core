from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
PRICE = Numeric(14, 4)

instruments = Table(
    "instruments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", String(10), nullable=False),
    Column("exchange", String(10), nullable=False),
    Column("symbol", String(40), nullable=False),
    Column("name", String(200)),
    Column("yf_symbol", String(40)),
    Column("kite_token", BigInteger),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),
    CheckConstraint("kind in ('stock', 'index')", name="ck_instruments_kind"),
)

daily_bars = Table(
    "daily_bars",
    metadata,
    Column("instrument_id", Integer, ForeignKey("instruments.id"), primary_key=True),
    Column("trade_date", Date, primary_key=True),
    Column("source", String(10), primary_key=True),
    Column("open", PRICE, nullable=False),
    Column("high", PRICE, nullable=False),
    Column("low", PRICE, nullable=False),
    Column("close", PRICE, nullable=False),
    Column("volume", BigInteger, nullable=False),
    Column("price_basis", String(20), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("source in ('yfinance', 'kite', 'vendor')", name="ck_daily_bars_source"),
    CheckConstraint("price_basis in ('split_adjusted', 'raw')", name="ck_daily_bars_price_basis"),
)

corporate_actions = Table(
    "corporate_actions",
    metadata,
    Column("instrument_id", Integer, ForeignKey("instruments.id"), primary_key=True),
    Column("ex_date", Date, primary_key=True),
    Column("kind", String(10), primary_key=True),
    Column("source", String(10), primary_key=True),
    Column("ratio_from", Numeric(12, 4), nullable=False),
    Column("ratio_to", Numeric(12, 4), nullable=False),
    CheckConstraint("kind in ('split', 'bonus')", name="ck_corporate_actions_kind"),
)

index_membership = Table(
    "index_membership",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("index_name", String(40), nullable=False),
    Column("instrument_id", Integer, ForeignKey("instruments.id"), nullable=False),
    Column("valid_from", Date, nullable=False),
    Column("valid_to", Date),
    Column("is_backfilled_assumption", Boolean, nullable=False),
    UniqueConstraint("index_name", "instrument_id", "valid_from", name="uq_index_membership"),
)

trading_calendar = Table(
    "trading_calendar",
    metadata,
    Column("trade_date", Date, primary_key=True),
    Column("is_trading_day", Boolean, nullable=False),
    Column("note", Text),
    Column("source", String(20), nullable=False),
)

option_contracts = Table(
    "option_contracts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("underlying", String(20), nullable=False),
    Column("expiry", Date, nullable=False),
    Column("strike", Numeric(12, 2), nullable=False),
    Column("option_type", String(2), nullable=False),
    Column("lot_size", Integer, nullable=False),
    Column("tradingsymbol", String(60), nullable=False),
    Column("kite_token", BigInteger, nullable=False),
    Column("first_seen", Date, nullable=False),
    Column("last_seen", Date, nullable=False),
    UniqueConstraint("tradingsymbol", "expiry", name="uq_option_contracts_symbol_expiry"),
    CheckConstraint("option_type in ('CE', 'PE')", name="ck_option_contracts_type"),
)

option_snapshots = Table(
    "option_snapshots",
    metadata,
    Column("contract_id", Integer, ForeignKey("option_contracts.id"), primary_key=True),
    Column("snapshot_at", DateTime(timezone=True), primary_key=True),
    Column("last_price", PRICE, nullable=False),
    Column("bid", PRICE),
    Column("ask", PRICE),
    Column("volume", BigInteger),
    Column("oi", BigInteger),
    Column("underlying_price", PRICE, nullable=False),
    Column("source", String(10), nullable=False),
)

kite_session = Table(
    "kite_session",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kite_user_id", String(40), nullable=False),
    Column("access_token_encrypted", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
)

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job", String(40), nullable=False),
    Column("run_date", Date, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String(10), nullable=False),
    Column("source", String(20)),
    Column("counts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("message", Text, nullable=False, server_default=text("''")),
    CheckConstraint(
        "status in ('running', 'ok', 'partial', 'skipped', 'failed')", name="ck_ingestion_runs_status"
    ),
)

data_quality_issues = Table(
    "data_quality_issues",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("ingestion_runs.id"), nullable=False),
    Column("instrument_id", Integer, ForeignKey("instruments.id")),
    Column("contract_id", Integer, ForeignKey("option_contracts.id")),
    Column("trade_date", Date, nullable=False),
    Column("check_name", String(40), nullable=False),
    Column("severity", String(10), nullable=False),
    Column("detail", Text, nullable=False),
    CheckConstraint("severity in ('info', 'warn', 'error')", name="ck_issues_severity"),
)
