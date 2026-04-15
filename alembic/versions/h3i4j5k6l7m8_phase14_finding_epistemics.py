"""Phase 14 — finding epistemics, disputes, audit trail.

Revision ID: h3i4j5k6l7m8
Revises: g1h2i3j4k5l6
Create Date: 2026-04-15

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h3i4j5k6l7m8"
down_revision: Union[str, Sequence[str], None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite batch mode requires named FK constraints (not inline on Column).
    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("subject_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.create_index("ix_evidence_entries_subject_id", ["subject_id"])
        batch_op.add_column(sa.Column("source_type", sa.String(64), nullable=False, server_default="other"))
        batch_op.add_column(sa.Column("source_title", sa.String(1024), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("source_publisher", sa.String(512), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("source_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("date_discovered", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("claim_text", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("claim_summary", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("claim_status", sa.String(32), nullable=False, server_default="active")
        )
        batch_op.add_column(
            sa.Column("review_status", sa.String(32), nullable=False, server_default="pending")
        )
        batch_op.add_column(sa.Column("review_notes", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("is_publicly_renderable", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("display_label", sa.String(256), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("source_excerpt", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("source_hash", sa.String(128), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("linked_entities_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("jurisdiction", sa.String(512), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("case_number", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("court", sa.String(256), nullable=True))
        batch_op.add_column(sa.Column("ingest_method", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("receipt_id", sa.String(512), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("classification_basis", sa.String(64), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("corroboration_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("contradiction_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_foreign_key(
            "fk_evidence_entries_subject_id_subject_profiles",
            "subject_profiles",
            ["subject_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "dispute_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("finding_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("subject_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("submitted_by", sa.String(64), nullable=False),
        sa.Column("submission_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispute_type", sa.String(64), nullable=False),
        sa.Column("dispute_text", sa.Text(), nullable=False),
        sa.Column("supporting_source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("supporting_document_hash", sa.String(128), nullable=True),
        sa.Column("resolution_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("resolution_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(256), nullable=True),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["evidence_entries.id"],
            name="fk_dispute_records_finding_id_evidence_entries",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subject_profiles.id"],
            name="fk_dispute_records_subject_id_subject_profiles",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_dispute_records_finding_id", "dispute_records", ["finding_id"])

    op.create_table(
        "finding_audit_log",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("finding_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["evidence_entries.id"],
            name="fk_finding_audit_log_finding_id_evidence_entries",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_finding_audit_log_finding_id", "finding_audit_log", ["finding_id"])


def downgrade() -> None:
    op.drop_index("ix_finding_audit_log_finding_id", table_name="finding_audit_log")
    op.drop_table("finding_audit_log")
    op.drop_index("ix_dispute_records_finding_id", table_name="dispute_records")
    op.drop_table("dispute_records")

    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.drop_column("contradiction_count")
        batch_op.drop_column("corroboration_count")
        batch_op.drop_column("classification_basis")
        batch_op.drop_column("receipt_id")
        batch_op.drop_column("ingest_method")
        batch_op.drop_column("court")
        batch_op.drop_column("case_number")
        batch_op.drop_column("jurisdiction")
        batch_op.drop_column("linked_entities_json")
        batch_op.drop_column("source_hash")
        batch_op.drop_column("source_excerpt")
        batch_op.drop_column("display_label")
        batch_op.drop_column("is_publicly_renderable")
        batch_op.drop_column("review_notes")
        batch_op.drop_column("review_status")
        batch_op.drop_column("claim_status")
        batch_op.drop_column("claim_summary")
        batch_op.drop_column("claim_text")
        batch_op.drop_column("date_discovered")
        batch_op.drop_column("source_date")
        batch_op.drop_column("source_publisher")
        batch_op.drop_column("source_title")
        batch_op.drop_column("source_type")
        batch_op.drop_constraint(
            "fk_evidence_entries_subject_id_subject_profiles", type_="foreignkey"
        )
        batch_op.drop_index("ix_evidence_entries_subject_id")
        batch_op.drop_column("subject_id")
