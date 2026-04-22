"""Add case_narratives table for AI-generated investigation summaries.

Revision ID: i1j2k3l4m5n6
Revises: h3i4j5k6l7m8
Create Date: 2026-04-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i1j2k3l4m5n6"
down_revision: Union[str, Sequence[str], None] = "h3i4j5k6l7m8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_narratives",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.String(length=64), nullable=False),
        sa.Column("narrative_text", sa.Text(), nullable=False),
        sa.Column("model_used", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
        sa.Index("idx_case_narratives_case_file_id", "case_file_id"),
    )


def downgrade() -> None:
    op.drop_table("case_narratives")
