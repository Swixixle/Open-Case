"""
SQLite for development; swap URL via DATABASE_URL for Postgres (e.g. Render).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from models import Base

logger = logging.getLogger(__name__)

_raw_url = os.getenv("DATABASE_URL")
if not _raw_url or not str(_raw_url).strip():
    logger.critical(
        "DATABASE_URL is not set; falling back to sqlite:///./open_case.db so the app can start."
    )
    DATABASE_URL = "sqlite:///./open_case.db"
elif str(_raw_url).strip().lower().startswith("sqlite"):
    DATABASE_URL = str(_raw_url).strip()
    logger.warning(
        "DATABASE_URL uses SQLite (%s). OK for local dev; use Postgres in production if needed.",
        DATABASE_URL[:48] + "..." if len(DATABASE_URL) > 48 else DATABASE_URL,
    )
elif str(_raw_url).strip().startswith(("postgresql", "postgres")):
    DATABASE_URL = str(_raw_url).strip()
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        logger.info("Normalized postgres:// URL to postgresql:// for SQLAlchemy.")
    logger.info(
        "DATABASE_URL uses PostgreSQL (prefix: %s).",
        DATABASE_URL[:32] + "..." if len(DATABASE_URL) > 32 else DATABASE_URL,
    )
else:
    DATABASE_URL = str(_raw_url).strip()
    logger.info(
        "DATABASE_URL is set (prefix: %s).",
        DATABASE_URL[:32] + "..." if len(DATABASE_URL) > 32 else DATABASE_URL,
    )

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_enforce_foreign_keys(
        dbapi_connection: object, _connection_record: object
    ) -> None:
        # Default SQLite connections ignore FK constraints; enable so CASCADE and DELETE work.
        cur = dbapi_connection.cursor()  # type: ignore[union-attr]
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
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
    try:
        logger.info("Running alembic upgrade head")
        command.upgrade(cfg, "head")
        logger.info("Migration complete")
    except Exception as e:
        logger.exception(
            "Migration failed — continuing with existing schema: %s",
            e,
        )


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
    # run_migrations()  # TEMP: Disabled for local dev - migrations already applied
    try:
        backfill_donor_fingerprint_canonical_ids()
    except Exception:
        logger.exception(
            "Donor fingerprint canonical_id backfill failed; continuing without backfill."
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
