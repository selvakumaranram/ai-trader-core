"""shadow trading signals and trades

Revision ID: f2b4c6d8e013
Revises: e7a1c3d5b902
Create Date: 2026-11-05 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2b4c6d8e013"
down_revision: Union[str, Sequence[str], None] = "e7a1c3d5b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRICE = sa.Numeric(14, 4)


def upgrade() -> None:
    op.create_table(
        "shadow_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("rule", sa.String(20), nullable=False),
        sa.Column("side", sa.String(2), nullable=False),
        sa.Column("underlying", sa.String(20), nullable=False, server_default=sa.text("'NIFTY'")),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("stop", PRICE, nullable=False),
        sa.Column("target", PRICE, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(10), nullable=False, server_default=sa.text("'new'")),
        sa.Column("user_view", sa.String(10)),
        sa.Column("user_view_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("signal_date", "rule", name="uq_shadow_signals_day_rule"),
    )
    op.create_table(
        "shadow_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("shadow_signals.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("rule", sa.String(20), nullable=False),
        sa.Column("side", sa.String(2), nullable=False),
        sa.Column("tradingsymbol", sa.String(60), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", sa.Numeric(12, 2), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", PRICE, nullable=False),
        sa.Column("underlying_entry", PRICE, nullable=False),
        sa.Column("stop", PRICE, nullable=False),
        sa.Column("target", PRICE, nullable=False),
        sa.Column("mark_price", PRICE),
        sa.Column("underlying_mark", PRICE),
        sa.Column("marked_at", sa.DateTime(timezone=True)),
        sa.Column("exit_at", sa.DateTime(timezone=True)),
        sa.Column("exit_price", PRICE),
        sa.Column("underlying_exit", PRICE),
        sa.Column("exit_reason", sa.String(20)),
        sa.Column("status", sa.String(10), nullable=False, server_default=sa.text("'open'")),
        sa.Column("charges", sa.Numeric(12, 2)),
        sa.Column("pnl_per_lot", sa.Numeric(14, 2)),
        sa.Column("note", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("shadow_trades")
    op.drop_table("shadow_signals")
