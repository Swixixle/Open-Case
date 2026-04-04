"""Phase 10C — evidence donor_type + pattern_alert_records diagnostics_json

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-04-03

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "evidence_entries",
        sa.Column("donor_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "pattern_alert_records",
        sa.Column("diagnostics_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pattern_alert_records", "diagnostics_json")
    op.drop_column("evidence_entries", "donor_type")
