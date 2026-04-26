"""Add bill_activity for Congress.gov sponsored/cosponsored legislation.

Revision ID: m5n6o7p8q9r0
Revises: l4m5n6o7p8q9
Create Date: 2026-04-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m5n6o7p8q9r0"
down_revision: Union[str, Sequence[str], None] = "l4m5n6o7p8q9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bill_activity",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=32), nullable=True),
        sa.Column("bill_number", sa.String(length=32), nullable=False),
        sa.Column("congress", sa.Integer(), nullable=False),
        sa.Column("bill_type", sa.String(length=16), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("introduced_date", sa.Date(), nullable=True),
        sa.Column("cosponsored_date", sa.Date(), nullable=True),
        sa.Column("current_status", sa.String(length=64), nullable=True),
        sa.Column("subject_policy_area", sa.String(length=128), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_bill_activity_bioguide_id"),
        "bill_activity",
        ["bioguide_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bill_activity_case_file_id"),
        "bill_activity",
        ["case_file_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_bill_activity_case_file_id"), table_name="bill_activity")
    op.drop_index(op.f("ix_bill_activity_bioguide_id"), table_name="bill_activity")
    op.drop_table("bill_activity")
