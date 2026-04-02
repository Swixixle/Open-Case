"""Phase 10B — donor fingerprint: canonical_id, resolution_method, normalized_name.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-01

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("donor_fingerprints", schema=None) as batch_op:
        batch_op.add_column(sa.Column("canonical_id", sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column("resolution_method", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("normalized_name", sa.String(length=512), nullable=True))
        batch_op.create_index("ix_donor_fingerprints_canonical_id", ["canonical_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("donor_fingerprints", schema=None) as batch_op:
        batch_op.drop_index("ix_donor_fingerprints_canonical_id")
        batch_op.drop_column("normalized_name")
        batch_op.drop_column("resolution_method")
        batch_op.drop_column("canonical_id")
