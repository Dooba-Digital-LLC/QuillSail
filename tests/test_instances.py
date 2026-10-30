"""Concurrent local instances must never share accounts, content or jobs."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from click.testing import CliRunner
from lxml import html
from sqlalchemy import select

from copydesk import instances, jobs, mail, notify, settings
from copydesk.cli import main
from copydesk.config import ConfigError, load_config
from copydesk.db import db
from copydesk.desktop import DesktopRuntime
from copydesk.models import Job, User


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(instances, 'default_data_dir', lambda: tmp_path / 'default')
    monkeypatch.setenv('QUILLSAIL_INSTANCE_HOME', str(tmp_path / 'manager'))
    return instances.InstanceRegistry()


def claim(browser, runtime, name):
    response = browser.get(runtime.launch_url(), timeout=10)
    csrf = html.fromstring(response.content).xpath('//input[@name="csrf_token"]/@value')[0]
    response = browser.post(runtime.url + '/setup/', data={'csrf_token': csrf, 'site_name': name,
        'display_name': name, 'email': name.lower() + '@example.test', 'password': 'synthetic-multiple-instances-123', 'site_mode': 'hybrid'}, timeout=10)
    assert response.status_code == 200 and response.url.endswith('/dashboard/')


def test_simultaneous_instances_isolate_browser_accounts_databases_jobs_and_shutdown(registry):
    left = DesktopRuntime(registry.add('Alpha').root)
    right = DesktopRuntime(registry.add('Beta').root)
    try:
        left.start(with_worker=False)
        right.start(with_worker=False)
        assert left.url != right.url
        with requests.Session() as browser:
            browser.trust_env = False
            claim(browser, left, 'Alpha')
            claim(browser, right, 'Beta')
            assert b'Hello, Alpha' in browser.get(left.url + '/dashboard/').content
            assert b'Hello, Beta' in browser.get(right.url + '/dashboard/').content
            assert len({c.name for c in browser.cookies if c.name.startswith('quillsail_desktop_')}) == 2
            for runtime, name in ((left, 'Alpha'), (right, 'Beta')):
                with runtime.app.app_context():
                    assert settings.get('site.name') == name
                    assert db.session.scalar(select(User.email)) == name.lower() + '@example.test'
                    notify.queue(mail.Email(to='reader@example.test', subject=name, text=name))
                    db.session.commit()
                    assert db.session.scalar(select(Job)).payload['subject'] == name
            def send(runtime):
                with runtime.app.app_context():
                    return jobs.run_one()
            with ThreadPoolExecutor(2) as pool:
                assert list(pool.map(send, (left, right))) == [True, True]
            for runtime, name in ((left, 'Alpha'), (right, 'Beta')):
                config = load_config(runtime.config_path, use_environment=False)
                messages = list(config.mail.file_dir.glob('*.eml'))
                assert len(messages) == 1 and f'Subject: {name}' in messages[0].read_text()
            with left.app.app_context():
                engine, session = db.engine, db.session()
                with right.app.app_context():
                    assert db.engine is not engine and db.session() is not session
                    assert settings.get('site.name') == 'Beta'
                assert db.engine is engine and db.session() is session
            right.stop()
            assert b'Hello, Alpha' in browser.get(left.url + '/dashboard/').content
    finally:
        right.stop()
        left.stop()


def test_registry_preserves_legacy_default_and_rejects_duplicate_paths_and_names(registry):
    assert registry.list() == [registry.default]
    first = registry.add('Team News')
    original = first.config_path.read_bytes()
    with pytest.raises(ConfigError, match='name is already'):
        registry.add('Team News')
    with pytest.raises(ConfigError, match='separate data folder'):
        registry.add('Alias', first.root)
    with pytest.raises(ConfigError, match='separate data folder'):
        registry.add('Nested', first.root / 'nested')
    assert first.config_path.read_bytes() == original
    assert not registry.default.root.exists()
    assert registry.path.stat().st_mode & 0o077 == 0
    assert b'secret_key' not in registry.path.read_bytes()
    registry.path.write_text('{broken')
    with pytest.raises(ConfigError, match='registry could not be read'):
        registry.list()


def test_running_detection_does_not_mistake_a_stale_port_for_another_publication(registry):
    first, second = registry.add('One'), registry.add('Two')
    runtime = DesktopRuntime(first.root)
    try:
        runtime.start(with_worker=False)
        assert instances.running_url(first) == runtime.url
        (second.root / 'desktop.json').write_bytes((first.root / 'desktop.json').read_bytes())
        assert instances.running_url(second) == ''
    finally:
        runtime.stop()
    assert instances.running_url(first) == ''


def test_cli_and_gui_registry_share_instances_and_named_commands_ignore_server_environment(registry, monkeypatch):
    runner = CliRunner()
    result = runner.invoke(main, ['instance', 'create', 'Student Blog'])
    assert result.exit_code == 0, result.output
    item = registry.get('student-blog')
    result = runner.invoke(main, ['instance', 'list', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[1]['directory'] == str(item.root)
    monkeypatch.setenv('COPYDESK_CONFIG', '/does-not-exist/server.toml')
    monkeypatch.setenv('COPYDESK_DATABASE_URL', 'postgresql://never-connect@example.test/server')
    result = runner.invoke(main, ['--instance', item.id, 'jobs', 'list'])
    assert result.exit_code == 0, (result.output, result.exception)
    result = runner.invoke(main, ['--instance', item.id, '--config', str(item.config_path), 'check'])
    assert result.exit_code != 0 and 'rather than both' in result.output
    result = runner.invoke(main, ['--instance', item.id, 'update', '--check'])
    assert result.exit_code != 0 and 'updates are shared' in result.output
    result = runner.invoke(main, ['instance', 'add', 'Duplicate Folder', str(item.root)])
    assert result.exit_code != 0 and 'separate data folder' in result.output


def test_cli_runs_multiple_instances_and_cleans_up_all_listeners(registry, monkeypatch):
    for name in ('First', 'Second'):
        registry.add(name)
    stop = threading.Event()
    monkeypatch.setattr('copydesk.cli.Event', lambda: stop)
    seen = []
    original_start = DesktopRuntime.start
    def start(runtime, **kwargs):
        result = original_start(runtime, **kwargs)
        seen.append(runtime)
        if len(seen) == 2:
            assert all(requests.get(item.url + '/healthz', timeout=5).status_code == 200 for item in seen)
            stop.set()
        return result
    monkeypatch.setattr(DesktopRuntime, 'start', start)
    result = CliRunner().invoke(main, ['instance', 'run', 'first', 'second', '--no-browser', '--no-worker'])
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(seen) == 2 and all(runtime.server is None for runtime in seen)
    assert all(instances.running_url(item) == '' for item in registry.list())


def test_delete_keeps_files_by_default_and_can_reregister(registry):
    item = registry.add('Keep Me')
    original = item.config_path.read_bytes()
    assert registry.delete(item.id) == item
    assert registry.list() == [registry.default]
    assert item.config_path.read_bytes() == original
    assert registry.add('Restored', item.root, existing=True).root == item.root


def test_delete_data_erases_only_selected_folder_and_preserves_symlink_target(registry, tmp_path):
    item, other = registry.add('Delete Me'), registry.add('Keep Me')
    target = tmp_path / 'outside.txt'
    target.write_text('keep')
    (item.root / 'outside-link').symlink_to(target)
    registry.delete(item.id, delete_data=True)
    assert not item.root.exists()
    assert target.read_text() == 'keep'
    assert other.config_path.is_file()
    assert registry.list() == [registry.default, other]


def test_delete_refuses_instances_owned_by_another_process(registry):
    from copydesk.desktop import AlreadyRunning, InstanceLock

    item = registry.add('Locked')
    lock = InstanceLock(item.root / 'desktop.lock')
    lock.acquire()
    try:
        for delete_data in (False, True):
            with pytest.raises(AlreadyRunning):
                registry.delete(item.id, delete_data=delete_data)
            assert registry.get(item.id) == item
            assert item.config_path.is_file()
    finally:
        lock.release()


@pytest.mark.parametrize('delete_data', [False, True])
def test_default_deletion_persists_empty_registry_and_allows_new_instances(registry, delete_data):
    item = registry.default
    DesktopRuntime(item.root).initialize()
    registry.delete('default', delete_data=delete_data)
    reopened = instances.InstanceRegistry()
    assert reopened.list() == []
    assert item.root.exists() is not delete_data
    with pytest.raises(ConfigError, match='No instance named'):
        reopened.get('default')
    replacement = reopened.add('Replacement')
    assert reopened.list() == [replacement]
    if not delete_data:
        restored = reopened.add('Original', item.root, existing=True)
        assert restored.root == item.root


def test_delete_stops_owned_default_and_worker_without_stopping_other_instance(registry, monkeypatch):
    other = registry.add('Other')
    runtime, other_runtime = DesktopRuntime(registry.default.root), DesktopRuntime(other.root)
    try:
        runtime.start()
        other_runtime.start(with_worker=False)
        worker, listener = runtime.worker_thread, runtime.http_thread
        original_rmtree = instances.shutil.rmtree
        def erase(path, *args, **kwargs):
            assert runtime.server is None and not worker.is_alive() and not listener.is_alive()
            return original_rmtree(path, *args, **kwargs)
        monkeypatch.setattr(instances.shutil, 'rmtree', erase)
        registry.delete('default', delete_data=True, runtime=runtime)
        assert runtime.server is None and not worker.is_alive() and not listener.is_alive()
        assert not registry.default.root.exists()
        assert registry.list() == [other]
        assert requests.get(other_runtime.url + '/healthz', timeout=5).status_code == 200
    finally:
        runtime.stop()
        other_runtime.stop()


def test_delete_preserves_registration_and_files_if_stop_fails(registry, monkeypatch):
    runtime = DesktopRuntime(registry.default.root)
    runtime.initialize()
    def fail():
        raise RuntimeError('Could not stop the worker')
    monkeypatch.setattr(runtime, 'stop', fail)
    with pytest.raises(RuntimeError, match='Could not stop'):
        registry.delete('default', delete_data=True, runtime=runtime)
    assert registry.list() == [registry.default]
    assert registry.default.config_path.is_file()


def test_delete_rejects_a_different_runtime_before_stopping_it(registry):
    other = registry.add('Other')
    runtime = DesktopRuntime(registry.default.root)
    try:
        runtime.start(with_worker=False)
        with pytest.raises(ConfigError, match='does not match'):
            registry.delete(other.id, delete_data=True, runtime=runtime)
        assert runtime.server is not None and other.config_path.is_file()
    finally:
        runtime.stop()


def test_cli_can_delete_default_and_list_an_empty_registry(registry):
    runner = CliRunner()
    result = runner.invoke(main, ['instance', 'delete', 'default', '--yes'])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, ['instance', 'list', '--json'])
    assert result.exit_code == 0 and json.loads(result.output) == []


def test_cli_delete_requires_confirmation_and_explicit_data_deletion(registry):
    runner = CliRunner()
    item = registry.add('Disposable')
    result = runner.invoke(main, ['instance', 'delete', item.id, '--delete-data'], input='n\n')
    assert result.exit_code != 0
    assert registry.get(item.id) == item and item.config_path.is_file()
    result = runner.invoke(main, ['instance', 'delete', item.id], input='y\n')
    assert result.exit_code == 0, result.output
    assert 'Files kept' in result.output and item.config_path.is_file()
    item = registry.add('Disposable', item.root, existing=True)
    result = runner.invoke(main, ['instance', 'delete', item.id, '--delete-data', '--yes'])
    assert result.exit_code == 0, result.output
    assert not item.root.exists()


def test_delete_missing_folder_can_remove_stale_registration(registry):
    import shutil

    item = registry.add('Missing')
    shutil.rmtree(item.root)
    registry.delete(item.id)
    assert registry.list() == [registry.default]


def test_delete_data_refuses_replaced_publication_folder(registry):
    item = registry.add('Changed')
    (item.root / 'desktop.json').unlink()
    with pytest.raises(ConfigError, match='not a complete desktop publication'):
        registry.delete(item.id, delete_data=True)
    assert registry.get(item.id) == item
    assert item.config_path.is_file()
