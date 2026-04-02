"""phase_6_investigator_api_key

Revision ID: c901d4e2f8ab
Revises: a2f8b91c4d10
Create Date: 2026-04-01 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c901d4e2f8ab"
down_revision: Union[str, Sequence[str], None] = "a2f8b91c4d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("investigators", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hashed_api_key", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("api_key_created_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_investigators_hashed_api_key"),
            ["hashed_api_key"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("investigators", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_investigators_hashed_api_key"))
        batch_op.drop_column("api_key_created_at")
        batch_op.drop_column("hashed_api_key")
