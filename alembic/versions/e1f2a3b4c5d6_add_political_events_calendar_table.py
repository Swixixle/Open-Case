"""add political_events calendar table

Revision ID: e1f2a3b4c5d6
Revises: d5e6f7a8b9c0
Create Date: 2026-04-03

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "political_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_name", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("state_code", sa.String(length=2), nullable=True),
        sa.Column("buffer_days_pre", sa.Integer(), nullable=False),
        sa.Column("buffer_days_post", sa.Integer(), nullable=False),
        sa.Column("discount_factor", sa.Float(), nullable=False),
        sa.Column("congress", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_political_events_event_date"), "political_events", ["event_date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_political_events_event_date"), table_name="political_events")
    op.drop_table("political_events")
