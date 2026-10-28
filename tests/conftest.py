import os

import pytest
from sqlalchemy import select

from copydesk import mail, migrate, seed
from copydesk.app import create_app
from copydesk.db import db
from copydesk.models import User
from copydesk.views.setup import create_admin


@pytest.fixture
def database_url(tmp_path):
    server = os.environ.get("COPYDESK_TEST_DATABASE_URL")
    if server:
        from database_support import temporary_database

        with temporary_database(server) as url:
            yield url
    else:
        yield f"sqlite:///{tmp_path}/test.db"


@pytest.fixture
def app(tmp_path, database_url):
    app = create_app(overrides={
        'site': {'secret_key': 'test-secret-key-that-is-at-least-32-characters',
                 'base_url': 'http://localhost', 'data_dir': str(tmp_path)},
        'database': {'url': database_url},
        'mail': {'backend': 'memory'},
        'testing': True,
    })
    app.config['TESTING'] = True
    app.config['RATELIMIT_ENABLED'] = False
    mail.outbox.clear()
    with app.app_context():
        migrate.upgrade(db.engine)
        seed.seed('Example Newsletter')
        create_admin(email='editor@example.edu', name='Test Editor', password='correct-horse-battery-123')
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    mail.outbox.clear()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(app):
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == 'editor@example.edu'))
        return user.id


@pytest.fixture
def editor_client(client, app, admin):
    with app.app_context():
        version = db.session.get(User, admin).auth_version
    with client.session_transaction() as session:
        session['uid'] = admin
        session['uv'] = version
        session['_csrf'] = 'test-csrf'
    return client
