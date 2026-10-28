"""Alembic environment. Uses a connection handed in by app.db.migrate when the app upgrades itself
at start-up, otherwise builds an engine from GC_DB_URL (command-line use)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db import models  # noqa: F401  (register tables)
from app.db.session import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = engine_from_config(
        {"sqlalchemy.url": get_settings().db_url}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
