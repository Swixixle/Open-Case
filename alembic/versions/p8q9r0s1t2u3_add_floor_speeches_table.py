"""Add floor_speeches for Congress.gov Congressional Record issues.

Revision ID: p8q9r0s1t2u3
Revises: o7p8q9r0s1t2
Create Date: 2026-04-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p8q9r0s1t2u3"
down_revision: Union[str, Sequence[str], None] = "o7p8q9r0s1t2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "floor_speeches",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=32), nullable=True),
        sa.Column("congress", sa.Integer(), nullable=False),
        sa.Column("chamber", sa.String(length=16), nullable=False),
        sa.Column("speech_date", sa.Date(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("page_range", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("full_text_url", sa.Text(), nullable=False),
        sa.Column("topic_tags", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_floor_speeches_bioguide_id"), "floor_speeches", ["bioguide_id"]
    )
    op.create_index(
        op.f("ix_floor_speeches_case_file_id"), "floor_speeches", ["case_file_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_floor_speeches_case_file_id"), table_name="floor_speeches")
    op.drop_index(op.f("ix_floor_speeches_bioguide_id"), table_name="floor_speeches")
    op.drop_table("floor_speeches")
