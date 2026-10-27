import json
import socket
import sqlite3
import time

import pytest
import requests
from lxml import html
from sqlalchemy import select

from copydesk import migrate, seed
from copydesk.config import ConfigError, load_config
from copydesk.db import db, make_engine
from copydesk.desktop import FORMAT, AlreadyRunning, DesktopRuntime
from copydesk.models import User
from copydesk.views.setup import create_admin


@pytest.fixture
def runtime(tmp_path):
    instance = DesktopRuntime(tmp_path / "publication")
    instance.start(with_worker=False)
    yield instance
    instance.stop()


def claim(client, runtime):
    response = client.get(runtime.launch_url())
    assert response.status_code == 200
    assert not html.fromstring(response.content).xpath('//input[@name="code"]')
    csrf = html.fromstring(response.content).xpath('//input[@name="csrf_token"]/@value')[0]
    response = client.post(runtime.url + "/setup/", data={"csrf_token": csrf, "site_name": "Desktop Journal",
        "display_name": "Local Owner", "email": "owner@example.test", "password": "synthetic-desktop-owner-123", "site_mode": "hybrid"})
    assert response.status_code == 200 and response.url == runtime.url + "/dashboard/"


def test_launcher_creates_private_empty_instance_and_completes_browser_setup(runtime):
    config = load_config(runtime.config_path, use_environment=False)
    assert config.mail.backend == "file" and not config.proxy_hops
    assert runtime.root.stat().st_mode & 0o077 == 0
    assert runtime.config_path.stat().st_mode & 0o077 == 0
    with runtime.app.app_context():
        assert db.session.scalar(select(User.id)) is None
        assert migrate.current_revision(db.engine) == migrate.head_revision()
    with requests.Session() as browser:
        claim(browser, runtime)
        assert browser.get(runtime.url + "/dashboard/articles/").status_code == 200
    with runtime.app.app_context():
        assert db.session.scalar(select(User)).email == "owner@example.test"
    assert not list(config.mail.file_dir.iterdir())


def test_setup_ticket_is_one_use_expiring_and_does_not_replace_csrf(runtime):
    ticket = runtime.launch_url()
    browser = requests.Session()
    response = browser.get(ticket, allow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert requests.get(ticket).status_code == 403
    assert requests.get(runtime.url + "/_desktop/open?ticket=wrong").status_code == 403
    assert browser.post(runtime.url + "/setup/", data={"site_name": "Unprotected"}).status_code == 400
    next_ticket = runtime.launch_url()
    with runtime._ticket_lock:
        runtime._tickets = {key: time.monotonic() - 1 for key in runtime._tickets}
    assert browser.get(next_ticket).status_code == 403
    assert html.fromstring(requests.get(runtime.url + "/setup/").content).xpath('//input[@name="code"]')


def test_desktop_bootstrap_never_logs_in_existing_accounts(runtime):
    with requests.Session() as browser:
        claim(browser, runtime)
    response = requests.get(runtime.launch_url())
    assert "/login" in response.url
    assert "/dashboard/" in response.url


@pytest.mark.parametrize("headers", [
    {"Host": "attacker.example"}, {"Host": "localhost"}, {"Origin": "https://attacker.example"}, {"Origin": "null"},
])
def test_desktop_rejects_rebinding_and_cross_origin_requests(runtime, headers):
    response = requests.get(runtime.url + "/healthz", headers=headers)
    assert response.status_code == 403


def test_desktop_ignores_operator_environment_and_preserves_login_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("COPYDESK_DATABASE_URL", "postgresql://do-not-connect@example.test/production")
    monkeypatch.setenv("COPYDESK_BASE_URL", "https://example.test/live")
    monkeypatch.setenv("COPYDESK_MAIL_BACKEND", "smtp")
    monkeypatch.setenv("COPYDESK_MAIL_PASSWORD", "synthetic-environment-secret")
    monkeypatch.setenv("COPYDESK_SECRET_KEY", "synthetic-environment-key-at-least-32-characters")
    instance = DesktopRuntime(tmp_path / "local")
    try:
        instance.start(with_worker=False)
        original = instance.config_path.read_bytes()
        with requests.Session() as browser:
            claim(browser, instance)
            instance.stop()
            instance.start(with_worker=False)
            assert instance.config_path.read_bytes() == original
            assert b"synthetic-environment" not in original
            assert instance.app.extensions["copydesk"].config.mail.backend == "file"
            assert browser.get(instance.launch_url()).url == instance.url + "/dashboard/"
    finally:
        instance.stop()


def test_duplicate_start_and_stop_cannot_unlock_or_modify_the_running_instance(runtime):
    duplicate = DesktopRuntime(runtime.root)
    original = runtime.config_path.read_bytes()
    with pytest.raises(AlreadyRunning):
        duplicate.start(with_worker=False)
    duplicate.stop()
    with pytest.raises(AlreadyRunning):
        duplicate.start(with_worker=False)
    assert requests.get(runtime.url + "/healthz").status_code == 200
    assert runtime.config_path.read_bytes() == original


def test_existing_non_desktop_files_are_preserved(tmp_path):
    original = tmp_path / "copydesk.toml"
    original.write_text("existing protected operator configuration")
    instance = DesktopRuntime(tmp_path)
    for _ in range(2):
        with pytest.raises(ConfigError, match="already contains"):
            instance.start(with_worker=False)
    assert original.read_text() == "existing protected operator configuration"
    assert not instance.state_path.exists()


def test_occupied_port_falls_back_without_changing_the_other_listener(runtime):
    runtime.stop()
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        busy_port = other.getsockname()[1]
        runtime.state_path.write_text(json.dumps({"format": FORMAT, "port": busy_port}))
        runtime.start(with_worker=False)
        assert runtime.server.server_port != busy_port
        assert requests.get(runtime.url + "/healthz").status_code == 200
        assert other.getsockname()[1] == busy_port


def test_worker_and_http_stop_and_the_same_data_can_restart(tmp_path):
    instance = DesktopRuntime(tmp_path)
    try:
        instance.start()
        worker, http = instance.worker_thread, instance.http_thread
        assert worker.is_alive() and http.is_alive()
        instance.stop()
        assert not worker.is_alive() and not http.is_alive()
        instance.start(with_worker=False)
        assert requests.get(instance.url + "/healthz").status_code == 200
    finally:
        instance.stop()


def test_desktop_upgrade_backs_up_old_database_config_and_uploaded_files(tmp_path):
    config_path = tmp_path / "copydesk.toml"
    config_path.write_text('[site]\nsecret_key="synthetic-desktop-upgrade-secret-at-least-32-characters"\n[mail]\nbackend="file"\n')
    (tmp_path / "desktop.json").write_text(json.dumps({"format": FORMAT, "port": 0}))
    config = load_config(config_path, use_environment=False)
    config.data_dir.mkdir()
    engine = make_engine(config.database_url)
    migrate.upgrade(engine, "0004")
    engine.dispose()
    from copydesk.app import create_app

    app = create_app(config)
    with app.app_context():
        seed.seed("Older desktop publication")
        create_admin(email="older@example.test", name="Previous Owner", password="synthetic-older-owner-123")
        db.session.commit()
    db.session.remove()
    db.engine.dispose()
    config.uploads_dir.mkdir()
    (config.uploads_dir / "preserved.txt").write_text("old upload")
    original = config_path.read_bytes()
    instance = DesktopRuntime(tmp_path)
    try:
        instance.start(with_worker=False)
        backup = next((tmp_path / "backups").iterdir())
        assert backup.stat().st_mode & 0o077 == 0
        assert (backup / "copydesk.toml").read_bytes() == original
        assert (backup / "files.tar.gz").stat().st_size > 0
        with sqlite3.connect(backup / "copydesk.db") as connection:
            assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0] == "0004"
            assert connection.execute('SELECT email FROM users').fetchone()[0] == "older@example.test"
        with instance.app.app_context():
            assert migrate.current_revision(db.engine) == migrate.head_revision()
            assert db.session.scalar(select(User)).email == "older@example.test"
        assert (config.uploads_dir / "preserved.txt").read_text() == "old upload"
    finally:
        instance.stop()
