"""phase_3_signal_identity_and_audit

Revision ID: a2f8b91c4d10
Revises: fdf38be85272
Create Date: 2026-04-01 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2f8b91c4d10"
down_revision: Union[str, Sequence[str], None] = "fdf38be85272"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("adapter_name", sa.String(length=128), nullable=True))

    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("signal_identity_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("repeat_count", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("proximity_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("parse_warning", sa.Text(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_signals_signal_identity_hash"),
            ["signal_identity_hash"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_signal_identity_per_case",
            ["case_file_id", "signal_identity_hash"],
        )

    op.create_table(
        "signal_audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("performed_by", sa.String(length=256), nullable=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("old_weight", sa.Float(), nullable=True),
        sa.Column("new_weight", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("signal_audit_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_signal_audit_log_signal_id"), ["signal_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("signal_audit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_signal_audit_log_signal_id"))

    op.drop_table("signal_audit_log")

    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_constraint("uq_signal_identity_per_case", type_="unique")
        batch_op.drop_index(batch_op.f("ix_signals_signal_identity_hash"))
        batch_op.drop_column("parse_warning")
        batch_op.drop_column("proximity_summary")
        batch_op.drop_column("repeat_count")
        batch_op.drop_column("signal_identity_hash")

    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.drop_column("adapter_name")
