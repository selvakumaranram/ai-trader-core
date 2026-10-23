"""backtest tables

Revision ID: c4d8e2f1a9b3
Revises: 91e795b9cd26
Create Date: 2026-10-23 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d8e2f1a9b3"
down_revision: Union[str, Sequence[str], None] = "91e795b9cd26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRICE = sa.Numeric(14, 4)


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("unseen_from", sa.Date()),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status in ('running', 'ok', 'failed')", name="ck_backtest_runs_status"),
    )
    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id")),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry_price", PRICE, nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("stop", PRICE, nullable=False),
        sa.Column("target", PRICE, nullable=False),
        sa.Column("exit_date", sa.Date(), nullable=False),
        sa.Column("exit_price", PRICE, nullable=False),
        sa.Column("exit_reason", sa.String(20), nullable=False),
        sa.Column("charges", PRICE, nullable=False),
        sa.Column("pnl", PRICE, nullable=False),
        sa.Column("r_multiple", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("period", sa.String(10), nullable=False),
    )
    op.create_index("ix_backtest_trades_run_id", "backtest_trades", ["run_id"])
    op.create_table(
        "backtest_equity",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("cash", PRICE, nullable=False),
        sa.Column("invested_value", PRICE, nullable=False),
        sa.Column("equity", PRICE, nullable=False),
        sa.Column("benchmark_equity", PRICE, nullable=False),
    )
    op.create_table(
        "backtest_index_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry", PRICE, nullable=False),
        sa.Column("stop", PRICE, nullable=False),
        sa.Column("target", PRICE, nullable=False),
        sa.Column("exit_date", sa.Date(), nullable=False),
        sa.Column("exit", PRICE, nullable=False),
        sa.Column("exit_reason", sa.String(20), nullable=False),
        sa.Column("r_multiple", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
    )
    op.create_index("ix_backtest_index_signals_run_id", "backtest_index_signals", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_backtest_index_signals_run_id", table_name="backtest_index_signals")
    op.drop_table("backtest_index_signals")
    op.drop_table("backtest_equity")
    op.drop_index("ix_backtest_trades_run_id", table_name="backtest_trades")
    op.drop_table("backtest_trades")
    op.drop_table("backtest_runs")
