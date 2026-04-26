"""Add ethics_issues for OCE / conduct oversight index rows.

Revision ID: r0s1t2u3v4w5
Revises: p8q9r0s1t2u3
Create Date: 2026-04-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r0s1t2u3v4w5"
down_revision: Union[str, Sequence[str], None] = "p8q9r0s1t2u3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ethics_issues",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=32), nullable=True),
        sa.Column("issue_type", sa.String(length=64), nullable=False),
        sa.Column("chamber", sa.String(length=16), nullable=False),
        sa.Column("source_body", sa.String(length=64), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=True),
        sa.Column("subject_matter", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("disposition", sa.Text(), nullable=True),
        sa.Column("resolution_date", sa.Date(), nullable=True),
        sa.Column("epistemic_level", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_ethics_issues_bioguide_id"),
        "ethics_issues",
        ["bioguide_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ethics_issues_case_file_id"),
        "ethics_issues",
        ["case_file_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ethics_issues_case_file_id"), table_name="ethics_issues")
    op.drop_index(op.f("ix_ethics_issues_bioguide_id"), table_name="ethics_issues")
    op.drop_table("ethics_issues")
