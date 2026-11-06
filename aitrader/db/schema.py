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


backtest_runs = Table(
    "backtest_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("strategy", String(20), nullable=False),
    Column("start_date", Date),
    Column("end_date", Date),
    Column("unseen_from", Date),
    Column("params", JSONB, nullable=False),
    Column("status", String(10), nullable=False),
    Column("message", Text, nullable=False, server_default=text("''")),
    Column("summary", JSONB),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint("status in ('running', 'ok', 'failed')", name="ck_backtest_runs_status"),
)

backtest_trades = Table(
    "backtest_trades",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("instrument_id", Integer, ForeignKey("instruments.id")),
    Column("symbol", String(40), nullable=False),
    Column("signal_date", Date, nullable=False),
    Column("entry_date", Date, nullable=False),
    Column("entry_price", PRICE, nullable=False),
    Column("shares", Integer, nullable=False),
    Column("stop", PRICE, nullable=False),
    Column("target", PRICE),  # NULL: no fixed target (strategy A2)
    Column("exit_date", Date, nullable=False),
    Column("exit_price", PRICE, nullable=False),
    Column("exit_reason", String(20), nullable=False),
    Column("charges", PRICE, nullable=False),
    Column("pnl", PRICE, nullable=False),
    Column("r_multiple", Numeric(12, 4), nullable=False),
    Column("reason", Text, nullable=False),
    Column("facts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("period", String(10), nullable=False),
)

backtest_equity = Table(
    "backtest_equity",
    metadata,
    Column("run_id", Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("trade_date", Date, primary_key=True),
    Column("cash", PRICE, nullable=False),
    Column("invested_value", PRICE, nullable=False),
    Column("equity", PRICE, nullable=False),
    Column("benchmark_equity", PRICE, nullable=False),
)

backtest_index_signals = Table(
    "backtest_index_signals",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("symbol", String(40), nullable=False),
    Column("direction", String(5), nullable=False),
    Column("signal_date", Date, nullable=False),
    Column("entry_date", Date, nullable=False),
    Column("entry", PRICE, nullable=False),
    Column("stop", PRICE, nullable=False),
    Column("target", PRICE, nullable=False),
    Column("exit_date", Date, nullable=False),
    Column("exit", PRICE, nullable=False),
    Column("exit_reason", String(20), nullable=False),
    Column("r_multiple", Numeric(12, 4), nullable=False),
    Column("reason", Text, nullable=False),
    Column("period", String(10), nullable=False),
)


shadow_signals = Table(
    "shadow_signals",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("signal_date", Date, nullable=False),
    Column("rule", String(20), nullable=False),
    Column("side", String(4), nullable=False),  # CE | PE | BUY
    Column("underlying", String(20), nullable=False, server_default=text("'NIFTY'")),
    Column("close", PRICE, nullable=False),
    Column("stop", PRICE, nullable=False),
    Column("target", PRICE),  # NULL for a stock signal (trailing stop, no target)
    Column("reason", Text, nullable=False),
    Column("facts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", String(10), nullable=False, server_default=text("'new'")),  # new | entered | missed | skipped
    Column("note", Text),
    Column("user_view", String(10)),  # agree | disagree
    Column("user_view_at", DateTime(timezone=True)),
    Column("pick_note", Text),  # your-picks portfolio: "bought" or why it was skipped
    Column("ai_view", String(10)),  # pick | skip (AI picks portfolio)
    Column("ai_reason", Text),
    Column("ai_model", String(60)),
    Column("ai_note", Text),  # AI picks portfolio: "bought" or why it was skipped
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("signal_date", "rule", "underlying", name="uq_shadow_signals_day_rule_symbol"),
)

shadow_trades = Table(
    "shadow_trades",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("signal_id", Integer, ForeignKey("shadow_signals.id", ondelete="CASCADE"), nullable=False),
    Column("portfolio", String(10), nullable=False, server_default=text("'rule'")),  # rule | mine (your picks)
    Column("rule", String(20), nullable=False),
    Column("side", String(4), nullable=False),  # CE | PE | BUY
    Column("tradingsymbol", String(60), nullable=False),
    Column("kind", String(10), nullable=False, server_default=text("'option'")),  # option | stock
    Column("expiry", Date),
    Column("strike", Numeric(12, 2)),
    Column("lot_size", Integer, nullable=False),  # option lot size, or the number of shares for a stock
    Column("entry_at", DateTime(timezone=True), nullable=False),
    Column("entry_price", PRICE, nullable=False),
    Column("underlying_entry", PRICE, nullable=False),
    Column("stop", PRICE, nullable=False),
    Column("target", PRICE, nullable=False),
    Column("mark_price", PRICE),
    Column("underlying_mark", PRICE),
    Column("marked_at", DateTime(timezone=True)),
    Column("exit_at", DateTime(timezone=True)),
    Column("exit_price", PRICE),
    Column("underlying_exit", PRICE),
    Column("exit_reason", String(20)),
    Column("status", String(10), nullable=False, server_default=text("'open'")),  # open | closed
    Column("charges", Numeric(12, 2)),
    Column("pnl_per_lot", Numeric(14, 2)),
    Column("note", Text),
    Column("initial_stop", PRICE),
    Column("highest_close", PRICE),
    Column("r_multiple", Numeric(12, 4)),
    UniqueConstraint("signal_id", "portfolio", name="uq_shadow_trades_signal_portfolio"),
)
