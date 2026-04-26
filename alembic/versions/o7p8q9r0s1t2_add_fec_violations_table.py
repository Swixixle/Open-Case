"""Add fec_violations for FEC MUR / admin fine / ADR legal search.

Revision ID: o7p8q9r0s1t2
Revises: n6o7p8q9r0s1
Create Date: 2026-04-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o7p8q9r0s1t2"
down_revision: Union[str, Sequence[str], None] = "n6o7p8q9r0s1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fec_violations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("mur_number", sa.String(length=32), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=True),
        sa.Column("closed_date", sa.Date(), nullable=True),
        sa.Column("respondent_names", sa.Text(), nullable=False),
        sa.Column("subject_matter", sa.Text(), nullable=False),
        sa.Column("disposition", sa.Text(), nullable=True),
        sa.Column("fine_amount", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_fec_violations_case_file_id"),
        "fec_violations",
        ["case_file_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_fec_violations_case_file_id"), table_name="fec_violations")
    op.drop_table("fec_violations")
