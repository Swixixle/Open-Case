"""Phase 13 — subject taxonomy fields, epistemic levels, pilot cohort columns.

Revision ID: g1h2i3j4k5l6
Revises: e8f9a0b1c2d3, f2e3d4c5b6a7
Create Date: 2026-04-15

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, Sequence[str], None] = ("e8f9a0b1c2d3", "f2e3d4c5b6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subject_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("government_level", sa.String(length=32), nullable=False, server_default="federal")
        )
        batch_op.add_column(
            sa.Column("branch", sa.String(length=32), nullable=False, server_default="legislative")
        )
        batch_op.add_column(
            sa.Column("historical_depth", sa.String(length=32), nullable=False, server_default="career")
        )

    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("government_level", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("branch", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("pilot_cohort", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("summary_epistemic_level", sa.String(length=32), nullable=False, server_default="REPORTED")
        )

    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("epistemic_level", sa.String(length=32), nullable=False, server_default="REPORTED")
        )
        batch_op.add_column(
            sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )

    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("epistemic_level", sa.String(length=32), nullable=False, server_default="REPORTED")
        )
        batch_op.add_column(
            sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )

    with op.batch_alter_table("pattern_alert_records", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("epistemic_level", sa.String(length=32), nullable=False, server_default="REPORTED")
        )
        batch_op.add_column(
            sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("pattern_alert_records", schema=None) as batch_op:
        batch_op.drop_column("requires_human_review")
        batch_op.drop_column("epistemic_level")

    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_column("requires_human_review")
        batch_op.drop_column("epistemic_level")

    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.drop_column("requires_human_review")
        batch_op.drop_column("epistemic_level")

    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.drop_column("summary_epistemic_level")
        batch_op.drop_column("pilot_cohort")
        batch_op.drop_column("branch")
        batch_op.drop_column("government_level")

    with op.batch_alter_table("subject_profiles", schema=None) as batch_op:
        batch_op.drop_column("historical_depth")
        batch_op.drop_column("branch")
        batch_op.drop_column("government_level")
