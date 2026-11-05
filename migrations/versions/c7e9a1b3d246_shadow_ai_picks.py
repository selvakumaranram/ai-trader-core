"""shadow trading: AI picks (Gemini's view, reason and model per signal; AI portfolio note)

Revision ID: c7e9a1b3d246
Revises: b6d8f0a2c135
Create Date: 2026-11-05 23:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e9a1b3d246"
down_revision: Union[str, Sequence[str], None] = "b6d8f0a2c135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shadow_signals", sa.Column("ai_view", sa.String(10)))
    op.add_column("shadow_signals", sa.Column("ai_reason", sa.Text()))
    op.add_column("shadow_signals", sa.Column("ai_model", sa.String(60)))
    op.add_column("shadow_signals", sa.Column("ai_note", sa.Text()))


def downgrade() -> None:
    op.execute("DELETE FROM shadow_trades WHERE portfolio = 'ai'")
    for column in ("ai_note", "ai_model", "ai_reason", "ai_view"):
        op.drop_column("shadow_signals", column)
