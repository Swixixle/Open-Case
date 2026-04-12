"""Phase 12 — senator dossiers table

Revision ID: e8f9a0b1c2d3
Revises: d5e6f7a8b9c0
Create Date: 2026-04-12

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "senator_dossiers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=16), nullable=False),
        sa.Column("senator_name", sa.String(length=256), nullable=False),
        sa.Column("dossier_json", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("share_token", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_senator_dossiers_bioguide_id"),
        "senator_dossiers",
        ["bioguide_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_senator_dossiers_share_token"),
        "senator_dossiers",
        ["share_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_senator_dossiers_share_token"), table_name="senator_dossiers")
    op.drop_index(op.f("ix_senator_dossiers_bioguide_id"), table_name="senator_dossiers")
    op.drop_table("senator_dossiers")
