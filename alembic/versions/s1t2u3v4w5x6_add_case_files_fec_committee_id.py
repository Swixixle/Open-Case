"""Add optional FEC principal committee id on case_files for reinvestigation.

Revision ID: s1t2u3v4w5x6
Revises: r0s1t2u3v4w5
Create Date: 2026-04-26

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s1t2u3v4w5x6"
down_revision: Union[str, Sequence[str], None] = "r0s1t2u3v4w5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("fec_committee_id", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.drop_column("fec_committee_id")
