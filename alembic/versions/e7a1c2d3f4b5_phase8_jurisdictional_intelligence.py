"""phase_8_jurisdictional_intelligence

Revision ID: e7a1c2d3f4b5
Revises: d4e8f0a1b2c3
Create Date: 2026-04-01 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c2d3f4b5"
down_revision: Union[str, Sequence[str], None] = "d4e8f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "senator_committees",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bioguide_id", sa.String(length=16), nullable=False),
        sa.Column("committee_name", sa.String(length=512), nullable=False),
        sa.Column("committee_code", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bioguide_id",
            "committee_code",
            name="uq_senator_committee_code",
        ),
    )
    op.create_index(
        "ix_senator_committees_bioguide_id",
        "senator_committees",
        ["bioguide_id"],
    )

    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("jurisdictional_match", sa.Boolean(), nullable=False, server_default="0"),
        )
        batch_op.add_column(
            sa.Column(
                "matched_committees",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
        )

    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0.0"),
        )
        batch_op.add_column(sa.Column("confirmation_checks", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("confirmation_basis", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_column("confirmation_basis")
        batch_op.drop_column("confirmation_checks")
        batch_op.drop_column("relevance_score")

    with op.batch_alter_table("evidence_entries", schema=None) as batch_op:
        batch_op.drop_column("matched_committees")
        batch_op.drop_column("jurisdictional_match")

    op.drop_index("ix_senator_committees_bioguide_id", table_name="senator_committees")
    op.drop_table("senator_committees")
