"""Add financial_disclosures for annual / PTR context line items.

Revision ID: l4m5n6o7p8q9
Revises: k3l4m5n6o7p8
Create Date: 2026-04-25

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l4m5n6o7p8q9"
down_revision: Union[str, Sequence[str], None] = "k3l4m5n6o7p8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "financial_disclosures",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=32), nullable=True),
        sa.Column("filing_year", sa.Integer(), nullable=False),
        sa.Column("disclosure_type", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("value_range", sa.String(length=64), nullable=True),
        sa.Column("income_amount", sa.String(length=64), nullable=True),
        sa.Column("source_name", sa.String(length=512), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_financial_disclosures_bioguide_id"),
        "financial_disclosures",
        ["bioguide_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_disclosures_case_file_id"),
        "financial_disclosures",
        ["case_file_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_financial_disclosures_case_file_id"), table_name="financial_disclosures")
    op.drop_index(op.f("ix_financial_disclosures_bioguide_id"), table_name="financial_disclosures")
    op.drop_table("financial_disclosures")
