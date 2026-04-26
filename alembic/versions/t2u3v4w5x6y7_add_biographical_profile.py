"""Add biographical_profile table and case net worth estimates.

Revision ID: t2u3v4w5x6y7
Revises: s1t2u3v4w5x6
Create Date: 2026-04-26 03:30:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t2u3v4w5x6y7"
down_revision: Union[str, Sequence[str], None] = "s1t2u3v4w5x6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "biographical_profile",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=10), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("birth_city", sa.String(length=100), nullable=True),
        sa.Column("birth_state", sa.String(length=2), nullable=True),
        sa.Column("party", sa.String(length=50), nullable=True),
        sa.Column("current_office", sa.String(length=200), nullable=True),
        sa.Column("office_start_date", sa.Date(), nullable=True),
        sa.Column("previous_offices", sa.JSON(), nullable=True),
        sa.Column("education", sa.JSON(), nullable=True),
        sa.Column("military_service", sa.JSON(), nullable=True),
        sa.Column("employment_history", sa.JSON(), nullable=True),
        sa.Column("office_addresses", sa.JSON(), nullable=True),
        sa.Column("official_website", sa.String(length=500), nullable=True),
        sa.Column("social_media", sa.JSON(), nullable=True),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_biographical_profile_case_file_id"),
        "biographical_profile",
        ["case_file_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_biographical_profile_bioguide_id"),
        "biographical_profile",
        ["bioguide_id"],
        unique=False,
    )
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("estimated_net_worth_min", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("estimated_net_worth_max", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("net_worth_calculation_date", sa.Date(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.drop_column("net_worth_calculation_date")
        batch_op.drop_column("estimated_net_worth_max")
        batch_op.drop_column("estimated_net_worth_min")
    op.drop_index(
        op.f("ix_biographical_profile_bioguide_id"), table_name="biographical_profile"
    )
    op.drop_index(
        op.f("ix_biographical_profile_case_file_id"), table_name="biographical_profile"
    )
    op.drop_table("biographical_profile")
