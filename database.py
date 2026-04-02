"""
SQLite for development; swap URL via DATABASE_URL for Postgres (e.g. Render).
"""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./open_case.db")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def run_migrations() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(cfg, "head")


def backfill_donor_fingerprint_canonical_ids() -> None:
    """
    Repair rows migrated before Phase 10B.1: fill canonical_id from legacy key.
    Safe to run repeatedly.
    """
    from sqlalchemy import or_, select

    from engines.entity_resolution import resolve
    from models import DonorFingerprint

    batch_limit = 4000
    with SessionLocal() as session:
        rows = session.scalars(
            select(DonorFingerprint)
            .where(
                or_(
                    DonorFingerprint.canonical_id.is_(None),
                    DonorFingerprint.canonical_id == "",
                )
            )
            .limit(batch_limit)
        ).all()
        touched = False
        for fp in rows:
            leg = (fp.normalized_donor_key or "").strip()
            if not leg:
                continue
            ent = resolve(leg)
            fp.canonical_id = ent.canonical_id
            fp.resolution_method = ent.resolution_method
            if ent.normalized_name:
                fp.normalized_name = ent.normalized_name
            touched = True
        if touched:
            session.commit()


def init_db() -> None:
    """Apply Alembic migrations (replaces create_all)."""
    run_migrations()
    backfill_donor_fingerprint_canonical_ids()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
