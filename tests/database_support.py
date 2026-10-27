"""Create disposable databases without touching an existing application's data."""

from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@contextmanager
def temporary_database(server_url):
    url = make_url(server_url)
    backend = url.get_backend_name()
    if backend not in {'postgresql', 'mysql'}:
        raise ValueError('Integration databases must use PostgreSQL or MySQL/MariaDB.')
    name = f'copydesk_test_{uuid4().hex}'
    admin_url = url.set(database='postgres' if backend == 'postgresql' else None)
    engine = create_engine(admin_url, isolation_level='AUTOCOMMIT')
    quote = '"' if backend == 'postgresql' else '`'
    create = f'CREATE DATABASE {quote}{name}{quote}'
    if backend == 'mysql':
        create += ' CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(create)
        try:
            yield url.set(database=name).render_as_string(hide_password=False)
        finally:
            with engine.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE {quote}{name}{quote}')
    finally:
        engine.dispose()
