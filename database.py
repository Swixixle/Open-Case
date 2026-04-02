"""
SQLite for development; swap `connect_args` and URL for Postgres in one place.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base

# Postgres-ready: replace with postgresql+psycopg://user:pass@host/dbname
SQLALCHEMY_DATABASE_URL = "sqlite:///./open_case.db"

# Alias for external scripts (e.g. scripts/test_idempotency.py)
DATABASE_URL = SQLALCHEMY_DATABASE_URL

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def run_migrations() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)
    command.upgrade(cfg, "head")


def init_db() -> None:
    """Apply Alembic migrations (replaces create_all)."""
    run_migrations()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
