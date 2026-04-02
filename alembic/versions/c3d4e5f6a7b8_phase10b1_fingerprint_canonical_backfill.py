"""Phase 10B.1 — backfill donor_fingerprints canonical_id from legacy normalized_donor_key.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-02

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        from engines.entity_resolution import resolve
        from models import DonorFingerprint

        rows = session.scalars(
            select(DonorFingerprint).where(
                or_(
                    DonorFingerprint.canonical_id.is_(None),
                    DonorFingerprint.canonical_id == "",
                )
            )
        ).all()
        for fp in rows:
            leg = (fp.normalized_donor_key or "").strip()
            if not leg:
                continue
            ent = resolve(leg)
            fp.canonical_id = ent.canonical_id
            fp.resolution_method = ent.resolution_method
            if ent.normalized_name:
                fp.normalized_name = ent.normalized_name
        session.commit()
    finally:
        session.close()


def downgrade() -> None:
    pass
