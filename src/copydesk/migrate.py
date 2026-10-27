"""Database migrations, run with `copydesk upgrade` (like MediaWiki's update.php).

Every schema change ships as an Alembic migration in copydesk/migrations, so
upgrading QuillSail never means hand-editing the database.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def head_revision(url: str = "sqlite://") -> str | None:
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def needs_upgrade(engine: Engine) -> bool:
    return current_revision(engine) != head_revision(str(engine.url))


def upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = alembic_config(engine.url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)
