"""Add stock_trades for House/Senate PTR-style disclosure rows.

Revision ID: k3l4m5n6o7p8
Revises: j2k3l4m5n6o7
Create Date: 2026-04-25

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k3l4m5n6o7p8"
down_revision: Union[str, Sequence[str], None] = "j2k3l4m5n6o7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_trades",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["case_file_id"], ["case_files.id"], ondelete="CASCADE"),
        sa.Column("bioguide_id", sa.String(length=32), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("disclosure_date", sa.Date(), nullable=True),
        sa.Column("asset_name", sa.String(length=512), nullable=False),
        sa.Column("asset_ticker", sa.String(length=16), nullable=True),
        sa.Column("asset_type", sa.String(length=64), nullable=True),
        sa.Column("transaction_type", sa.String(length=32), nullable=False),
        sa.Column("amount_range", sa.String(length=64), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_stock_trades_bioguide_id"), "stock_trades", ["bioguide_id"], unique=False)
    op.create_index(op.f("ix_stock_trades_case_file_id"), "stock_trades", ["case_file_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_stock_trades_case_file_id"), table_name="stock_trades")
    op.drop_index(op.f("ix_stock_trades_bioguide_id"), table_name="stock_trades")
    op.drop_table("stock_trades")
