"""Database engine and session.

``db.session`` belongs to the active Flask application and application context,
removed when that context ends. Each publication has its own engine and pool.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app, g
from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

# Predictable constraint names keep migrations portable across databases.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(
            url, connect_args={"check_same_thread": False, "timeout": 30}
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800)


@dataclass
class Binding:
    engine: Engine
    session: scoped_session


class Database:
    """Resolve connections from the active app, never from the last app created."""

    @property
    def engine(self) -> Engine:
        return current_app.extensions["copydesk_db"].engine

    @property
    def session(self) -> scoped_session:
        return current_app.extensions["copydesk_db"].session

    def configure(self, url: str) -> Engine:
        app = current_app._get_current_object()
        if previous := app.extensions.get("copydesk_db"):
            previous.session.remove()
            previous.engine.dispose()
        self.init_app(app, url)
        return self.engine

    def init_app(self, app, url: str) -> None:
        engine = make_engine(url)
        sessions = scoped_session(sessionmaker(bind=engine, expire_on_commit=False),
                                  scopefunc=lambda: id(g._get_current_object()))
        app.extensions["copydesk_db"] = Binding(engine, sessions)

        @app.teardown_appcontext
        def _remove_session(_exc):
            sessions.remove()

    def create_all(self) -> None:
        from copydesk import models  # noqa: F401 - registers the tables

        Base.metadata.create_all(self.engine)


db = Database()
