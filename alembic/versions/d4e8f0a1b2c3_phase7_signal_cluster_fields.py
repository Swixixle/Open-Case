"""phase_7_signal_cluster_fields

Revision ID: d4e8f0a1b2c3
Revises: c901d4e2f8ab
Create Date: 2026-04-02 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c901d4e2f8ab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("direction_verified", sa.Boolean(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("temporal_class", sa.String(length=32), nullable=True),
        )
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        op.execute(
            sa.text(
                "UPDATE signals SET direction_verified = 1 WHERE direction_verified IS NULL"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE signals SET direction_verified = true WHERE direction_verified IS NULL"
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_column("temporal_class")
        batch_op.drop_column("direction_verified")
