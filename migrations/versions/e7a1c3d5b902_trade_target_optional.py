"""backtest trade target optional (strategy A2 has no fixed target)

Revision ID: e7a1c3d5b902
Revises: c4d8e2f1a9b3
Create Date: 2026-11-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "e7a1c3d5b902"
down_revision: Union[str, Sequence[str], None] = "c4d8e2f1a9b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("backtest_trades", "target", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM backtest_trades WHERE target IS NULL")
    op.alter_column("backtest_trades", "target", nullable=False)
