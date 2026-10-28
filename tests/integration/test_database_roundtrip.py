import json
import os

import pytest
from click.testing import CliRunner
from sqlalchemy import func, select

from copydesk import demo
from copydesk.app import create_app
from copydesk.cli import main
from copydesk.config import load_config
from copydesk.db import db
from copydesk.models import User

pytestmark = pytest.mark.integration


def canonical_tables(data):
    return {name: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
            for name, rows in data['tables'].items()}


def test_export_restore_on_actual_server_database(app, tmp_path, database_url):
    server = os.environ.get('COPYDESK_TEST_DATABASE_URL')
    if not server:
        pytest.skip('Requires a disposable PostgreSQL or MySQL/MariaDB server')
    from database_support import temporary_database

    def config_file(name, url):
        path = tmp_path / f'{name}.toml'
        path.write_text('[site]\nsecret_key = "database-roundtrip-test-key-at-least-32-characters"\n'
                        '[database]\nurl = ' + json.dumps(url) + '\n[mail]\nbackend = "memory"\n')
        return path

    with app.app_context():
        demo.run()
    runner = CliRunner()
    source = config_file('source', database_url)
    backup = tmp_path / 'source.json'
    result = runner.invoke(main, ['--config', str(source), 'export', str(backup)])
    assert result.exit_code == 0, result.output
    original = json.loads(backup.read_text())
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    with temporary_database(server) as target_url:
        target = config_file('target', target_url)
        target_app = None
        try:
            result = runner.invoke(main, ['--config', str(target), 'import', str(backup), '--yes'])
            assert result.exit_code == 0, (result.output, result.exception)
            restored = tmp_path / 'restored.json'
            result = runner.invoke(main, ['--config', str(target), 'export', str(restored)])
            assert result.exit_code == 0, result.output
            assert canonical_tables(json.loads(restored.read_text())) == canonical_tables(original)
            target_app = create_app(load_config(target))
            with target_app.app_context():
                previous = db.session.scalar(select(func.max(User.id)))
                user = User(email='after-restore@example.test', username='after-restore',
                            display_name='After restore', password_hash='not-a-login')
                db.session.add(user)
                db.session.commit()
                assert user.id > previous
        finally:
            if target_app is not None:
                with target_app.app_context():
                    db.session.remove()
                    db.engine.dispose()
