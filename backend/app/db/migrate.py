"""Run Alembic migrations programmatically (at start-up and in tests)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()
