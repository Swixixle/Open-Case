"""Partial unique index on subject_profiles.bioguide_id (real bioguides only).

Revision ID: j2k3l4m5n6o7
Revises: i1j2k3l4m5n6
Create Date: 2026-04-22

Empty string and NULL are allowed in multiple rows; only non-empty bioguide_id must be unique
(prevents duplicate cases for the same member).

Run scripts/dedupe_todd_young_duplicate_case_files.py if duplicate Y000064 cases remain.

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j2k3l4m5n6o7"
down_revision: Union[str, Sequence[str], None] = "i1j2k3l4m5n6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = sa.text("bioguide_id IS NOT NULL AND bioguide_id != ''")


def upgrade() -> None:
    op.create_index(
        "uq_subject_profiles_bioguide_id",
        "subject_profiles",
        ["bioguide_id"],
        unique=True,
        sqlite_where=_WHERE,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index("uq_subject_profiles_bioguide_id", table_name="subject_profiles")
