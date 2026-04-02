"""phase_9b_pattern_alert_records

Revision ID: a1b2c3d4e5f6
Revises: f8b9c0d1e2f3
Create Date: 2026-04-02 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pattern_alert_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("pattern_version", sa.String(length=32), nullable=False),
        sa.Column("donor_entity", sa.String(length=512), nullable=False),
        sa.Column("matched_officials", sa.Text(), nullable=False),
        sa.Column("matched_case_ids", sa.Text(), nullable=False),
        sa.Column("committee", sa.String(length=512), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("evidence_refs", sa.Text(), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pattern_alert_records_rule_id",
        "pattern_alert_records",
        ["rule_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pattern_alert_records_rule_id", table_name="pattern_alert_records")
    op.drop_table("pattern_alert_records")
