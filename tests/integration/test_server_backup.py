"""Native dump/restore against disposable PostgreSQL and MariaDB databases."""

import os

import pytest
from sqlalchemy import inspect, select, text, update

from copydesk.db import db
from copydesk.models import Setting
from copydesk.server_backup import DatabaseBackup
from copydesk.server_update import CommandRunner

pytestmark = pytest.mark.integration


def test_native_snapshot_restores_data_and_removes_a_failed_future_schema(app, tmp_path):
    if not os.environ.get("COPYDESK_TEST_DATABASE_URL"):
        pytest.skip("Requires a disposable PostgreSQL or MySQL/MariaDB server and native client utilities")
    with app.app_context():
        db.session.add(Setting(key="synthetic.rollback", value={"private": "synthetic original"}))
        db.session.commit()
        config = app.extensions["copydesk"].config
        snapshot = DatabaseBackup(config, CommandRunner())
        try:
            snapshot.preflight()
            snapshot.snapshot(tmp_path)
            with snapshot.engine.begin() as connection:
                connection.execute(update(Setting).where(Setting.key == "synthetic.rollback").values(value={"private": "changed"}))
                connection.execute(text("CREATE TABLE synthetic_future_table (id INTEGER PRIMARY KEY)"))
                connection.execute(text("ALTER TABLE users ADD COLUMN synthetic_future_column INTEGER"))
            db.session.remove()
            db.engine.dispose()
            snapshot.restore(tmp_path)
            with snapshot.engine.connect() as connection:
                assert connection.execute(select(Setting.value).where(Setting.key == "synthetic.rollback")).scalar() == {"private": "synthetic original"}
            assert "synthetic_future_table" not in inspect(snapshot.engine).get_table_names()
            assert "synthetic_future_column" not in {column["name"] for column in inspect(snapshot.engine).get_columns("users")}
            assert not list(tmp_path.glob(".database-auth-*"))
        finally:
            snapshot.close()
