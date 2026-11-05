"""shadow trading: stock trades (C3 signals) alongside NIFTY options

Revision ID: a5c7e9f1b024
Revises: f2b4c6d8e013
Create Date: 2026-11-05 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a5c7e9f1b024"
down_revision: Union[str, Sequence[str], None] = "f2b4c6d8e013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRICE = sa.Numeric(14, 4)


def upgrade() -> None:
    for table in ("shadow_signals", "shadow_trades"):
        op.alter_column(table, "side", type_=sa.String(4))
    op.alter_column("shadow_signals", "target", nullable=True)
    op.add_column("shadow_signals", sa.Column("note", sa.Text()))
    op.drop_constraint("uq_shadow_signals_day_rule", "shadow_signals", type_="unique")
    op.create_unique_constraint("uq_shadow_signals_day_rule_symbol", "shadow_signals",
                                ["signal_date", "rule", "underlying"])
    op.add_column("shadow_trades", sa.Column("kind", sa.String(10), nullable=False,
                                             server_default=sa.text("'option'")))
    op.alter_column("shadow_trades", "expiry", nullable=True)
    op.alter_column("shadow_trades", "strike", nullable=True)
    op.add_column("shadow_trades", sa.Column("initial_stop", PRICE))
    op.add_column("shadow_trades", sa.Column("highest_close", PRICE))
    op.add_column("shadow_trades", sa.Column("r_multiple", sa.Numeric(12, 4)))


def downgrade() -> None:
    op.execute("DELETE FROM shadow_trades WHERE kind = 'stock'")
    op.execute("DELETE FROM shadow_signals WHERE target IS NULL")
    for col in ("r_multiple", "highest_close", "initial_stop", "kind"):
        op.drop_column("shadow_trades", col)
    op.alter_column("shadow_trades", "strike", nullable=False)
    op.alter_column("shadow_trades", "expiry", nullable=False)
    op.drop_constraint("uq_shadow_signals_day_rule_symbol", "shadow_signals", type_="unique")
    op.create_unique_constraint("uq_shadow_signals_day_rule", "shadow_signals", ["signal_date", "rule"])
    op.drop_column("shadow_signals", "note")
    op.alter_column("shadow_signals", "target", nullable=False)
