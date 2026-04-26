"""Add committee_assignments for Congress.gov member committee data.

Revision ID: n6o7p8q9r0s1
Revises: m5n6o7p8q9r0
Create Date: 2026-04-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n6o7p8q9r0s1"
down_revision: Union[str, Sequence[str], None] = "m5n6o7p8q9r0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "committee_assignments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=32), nullable=False),
        sa.Column("congress", sa.Integer(), nullable=False),
        sa.Column("chamber", sa.String(length=16), nullable=True),
        sa.Column("committee_code", sa.String(length=16), nullable=False),
        sa.Column("committee_name", sa.String(length=256), nullable=False),
        sa.Column("committee_type", sa.String(length=32), nullable=True),
        sa.Column("subcommittee_name", sa.String(length=256), nullable=True),
        sa.Column("rank_in_party", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_committee_assignments_bioguide_id"),
        "committee_assignments",
        ["bioguide_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_committee_assignments_case_file_id"),
        "committee_assignments",
        ["case_file_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_committee_assignments_case_file_id"), table_name="committee_assignments"
    )
    op.drop_index(
        op.f("ix_committee_assignments_bioguide_id"), table_name="committee_assignments"
    )
    op.drop_table("committee_assignments")
