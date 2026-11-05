"""shadow trading: your-picks portfolio (trades per signal and portfolio, pick note on signals)

Revision ID: b6d8f0a2c135
Revises: a5c7e9f1b024
Create Date: 2026-11-05 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6d8f0a2c135"
down_revision: Union[str, Sequence[str], None] = "a5c7e9f1b024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shadow_signals", sa.Column("pick_note", sa.Text()))
    op.add_column("shadow_trades", sa.Column("portfolio", sa.String(10), nullable=False,
                                             server_default=sa.text("'rule'")))
    op.drop_constraint("shadow_trades_signal_id_key", "shadow_trades", type_="unique")
    op.create_unique_constraint("uq_shadow_trades_signal_portfolio", "shadow_trades", ["signal_id", "portfolio"])


def downgrade() -> None:
    op.execute("DELETE FROM shadow_trades WHERE portfolio <> 'rule'")
    op.drop_constraint("uq_shadow_trades_signal_portfolio", "shadow_trades", type_="unique")
    op.create_unique_constraint("shadow_trades_signal_id_key", "shadow_trades", ["signal_id"])
    op.drop_column("shadow_trades", "portfolio")
    op.drop_column("shadow_signals", "pick_note")
