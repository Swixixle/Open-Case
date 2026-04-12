from __future__ import annotations

import logging
import sys
from logging.config import fileConfig
from pathlib import Path

logger = logging.getLogger(__name__)

from alembic import context
from sqlalchemy import create_engine, pool

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from database import DATABASE_URL  # noqa: E402
from models import Base  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    kw: dict = {"poolclass": pool.NullPool}
    if DATABASE_URL.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
    connectable = create_engine(DATABASE_URL, **kw)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


try:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
except Exception:
    logger.exception("Alembic env: run_migrations_online/offline failed")
    raise
