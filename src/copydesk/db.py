"""Database engine and session.

``db.session`` is a scoped SQLAlchemy session: one per thread, removed at the
end of every request (and by the worker after every job).
"""

from __future__ import annotations

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


class Database:
    def __init__(self) -> None:
        self.engine: Engine | None = None
        self.session = scoped_session(sessionmaker(expire_on_commit=False))

    def configure(self, url: str) -> Engine:
        self.session.remove()
        if self.engine is not None:
            self.engine.dispose()
        self.engine = make_engine(url)
        self.session.configure(bind=self.engine)
        return self.engine

    def init_app(self, app, url: str) -> None:
        self.configure(url)

        @app.teardown_appcontext
        def _remove_session(_exc):
            self.session.remove()

    def create_all(self) -> None:
        from copydesk import models  # noqa: F401 - registers the tables

        Base.metadata.create_all(self.engine)


db = Database()
