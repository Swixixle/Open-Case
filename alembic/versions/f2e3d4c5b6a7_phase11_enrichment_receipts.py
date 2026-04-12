"""Phase 11 — Perplexity enrichment receipts and case last_enriched_at

Revision ID: f2e3d4c5b6a7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-09

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2e3d4c5b6a7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "enrichment_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("subject_name", sa.String(length=512), nullable=False),
        sa.Column("bioguide_id", sa.String(length=32), nullable=True),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("new_findings_count", sa.Integer(), nullable=False),
        sa.Column("is_delta", sa.Boolean(), nullable=False),
        sa.Column("signed_receipt", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("enrichment_receipts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_enrichment_receipts_case_file_id"),
            ["case_file_id"],
            unique=False,
        )

    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.drop_column("last_enriched_at")

    with op.batch_alter_table("enrichment_receipts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_enrichment_receipts_case_file_id"))

    op.drop_table("enrichment_receipts")
