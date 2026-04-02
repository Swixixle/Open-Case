"""phase_9_credential_lda_fingerprint_baseline

Revision ID: f8b9c0d1e2f3
Revises: e7a1c2d3f4b5
Create Date: 2026-04-01 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "e7a1c2d3f4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_source_statuses", sa.Text(), nullable=True))

    op.create_table(
        "donor_fingerprints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("normalized_donor_key", sa.String(length=512), nullable=False),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("official_name", sa.String(length=512), nullable=False),
        sa.Column("bioguide_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_donor_fingerprints_normalized_donor_key",
        "donor_fingerprints",
        ["normalized_donor_key"],
    )
    op.create_index(
        "ix_donor_fingerprints_case_file_id",
        "donor_fingerprints",
        ["case_file_id"],
    )

    op.create_table(
        "investigation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signals_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_donors", sa.Text(), nullable=False, server_default="[]"),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investigation_runs_case_run",
        "investigation_runs",
        ["case_file_id", "run_at"],
    )

    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cross_case_appearances", sa.Integer(), nullable=False, server_default="0"),
        )
        batch_op.add_column(sa.Column("cross_case_officials", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("weight_delta", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("new_top_signal", sa.Boolean(), nullable=False, server_default="0"),
        )
        batch_op.add_column(
            sa.Column("first_appearance", sa.Boolean(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_column("first_appearance")
        batch_op.drop_column("new_top_signal")
        batch_op.drop_column("weight_delta")
        batch_op.drop_column("cross_case_officials")
        batch_op.drop_column("cross_case_appearances")

    op.drop_index("ix_investigation_runs_case_run", table_name="investigation_runs")
    op.drop_table("investigation_runs")

    op.drop_index("ix_donor_fingerprints_case_file_id", table_name="donor_fingerprints")
    op.drop_index("ix_donor_fingerprints_normalized_donor_key", table_name="donor_fingerprints")
    op.drop_table("donor_fingerprints")

    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.drop_column("last_source_statuses")
