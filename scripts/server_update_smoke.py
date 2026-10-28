"""Exercise real wheel/venv, HTTP, migration and worker updates in a disposable instance.

Only the systemd lifecycle is substituted: real child processes run as the
current user. No system services, external mail or publication data are used.
The interpreter running this script is trusted as the base Python: CI and
development interpreters live in group- or world-writable tool caches, which a
production updater rightly refuses. Disposable releases are still checked.
"""

import argparse
import base64
import csv
import hashlib
import io
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from copydesk import server_update
from copydesk.cli import main as cli
from copydesk.server_update import (
    CommandRunner,
    PythonDeployment,
    ServerProfile,
    ServerUpdater,
    harden_release,
    private_json,
    runtime_environment,
)
from copydesk.updater import Update, UpdateError


def synthetic_wheel(source, destination, version):
    """Repack a local test version, including a valid wheel RECORD."""
    files = {}
    with zipfile.ZipFile(source) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
        old_dist = metadata_name.split('/')[0]
        old_version = old_dist.removeprefix('copydesk-').removesuffix('.dist-info')
        new_dist = f'copydesk-{version}.dist-info'
        for name in archive.namelist():
            if name.endswith('.dist-info/RECORD'):
                continue
            content = archive.read(name)
            if name == 'copydesk/__init__.py':
                content = content.replace(old_version.encode(), version.encode())
            if name == metadata_name:
                content = content.replace(f'Version: {old_version}\n'.encode(), f'Version: {version}\n'.encode())
            files[name.replace(old_dist, new_dist)] = content
    record = io.StringIO(newline='')
    writer = csv.writer(record)
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
        writer.writerow([name, 'sha256=' + digest, len(content)])
    writer.writerow([new_dist + '/RECORD', '', ''])
    files[new_dist + '/RECORD'] = record.getvalue().encode()
    path = destination / f'copydesk-{version}-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


class Processes(PythonDeployment):
    def __init__(self, profile, run, port):
        super().__init__(profile, run)
        self.port, self.children, self.logs = port, {}, []

    def preflight(self):
        assert self.current.is_symlink()

    def start(self, *, worker=False):
        name = 'worker' if worker else 'web'
        child = self.children.get(name)
        if child and child.poll() is None:
            return
        environment = {**runtime_environment(), 'COPYDESK_CONFIG': str(self.profile.at('config'))}
        if worker:
            arguments = [self.current / 'venv/bin/quillsail', 'worker', '--poll', '0.1']
        else:
            arguments = [self.current / 'venv/bin/gunicorn', '--workers', '1', '--bind', f'127.0.0.1:{self.port}', 'copydesk.wsgi:app']
        log = (self.profile.at('backup_root') / (name + '.log')).open('ab')
        self.logs.append(log)
        self.children[name] = subprocess.Popen([str(arg) for arg in arguments], env=environment, cwd=self.profile.at('instance_root'),
                                               stdout=log, stderr=log, start_new_session=True)

    def stop(self):
        for child in self.children.values():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
        self.children.clear()
        for handle in self.logs:
            handle.close()
        self.logs.clear()

    def worker_active(self):
        child = self.children.get('worker')
        return child is not None and child.poll() is None

    def ownership(self):
        return os.geteuid(), os.getegid()


class LocalPackages:
    def __init__(self, wheel):
        self.wheel = wheel

    def download(self, update, destination):
        content = self.wheel.read_bytes()
        assert hashlib.sha256(content).hexdigest() == update.sha256
        destination.write_bytes(content)
        return destination


def trust_base_interpreter():
    """Skip the protection check only for this script's own base Python installation."""
    checked = server_update.trusted_code
    base = Path(sys.base_prefix).resolve()

    def trusted_code(path):
        if not Path(path).resolve().is_relative_to(base):
            checked(path)

    return patch.object(server_update, 'trusted_code', trusted_code)


def main(source):
    if os.name != 'posix':
        raise SystemExit('The managed server smoke test requires a POSIX host.')
    with trust_base_interpreter():
        exercise(source)


def exercise(source):
    run = CommandRunner()
    with tempfile.TemporaryDirectory(prefix='quillsail-server-smoke-') as folder:
        root = Path(folder)
        application, instance, backups = root / 'application', root / 'instance', root / 'backups'
        old = application / 'releases/initial'
        old.mkdir(parents=True)
        backups.mkdir()
        old_wheel = synthetic_wheel(source, root, '0.0.1')
        new_wheel = synthetic_wheel(source, root, '0.0.2')
        run([sys.executable, '-m', 'venv', old / 'venv'])
        run([old / 'venv/bin/python', '-m', 'pip', 'install', '--quiet', str(old_wheel) + '[server]'])
        harden_release(old)
        (application / 'current').symlink_to(old)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        run([old / 'venv/bin/quillsail', 'init', '--dir', instance, '--site-name', 'Synthetic update test',
             '--base-url', f'http://127.0.0.1:{port}/publication', '--admin-name', 'Synthetic Owner',
             '--admin-email', 'owner@example.test', '--admin-password', 'synthetic-update-password-123', '--yes'])
        publication_file = instance / 'data/uploads/synthetic.txt'
        publication_file.parent.mkdir(parents=True, exist_ok=True)
        publication_file.write_text('original private upload')
        profile_path = root / 'update.json'
        private_json(profile_path, {'format': 'quillsail.server-update.v1', 'kind': 'python',
                     'install_root': str(application), 'instance_root': str(instance), 'config': str(instance / 'copydesk.toml'),
                     'backup_root': str(backups), 'service_user': 'unused-synthetic-account',
                     'web_service': 'synthetic-web', 'worker_service': 'synthetic-worker',
                     'health_url': f'http://127.0.0.1:{port}/publication/healthz', 'health_timeout': 20})
        profile = ServerProfile.load(profile_path)
        services = Processes(profile, run, port)
        controller = ServerUpdater(profile, run=run, client=LocalPackages(new_wheel), deployment=services, require_privileges=False)
        # Migrations run as this test user; production uses runuser/systemd.
        services.as_service = lambda arguments: run(arguments, env=runtime_environment())
        controller.report = print
        release = Update('0.0.1', '0.0.2', 'server', 'offline', new_wheel.name, new_wheel.stat().st_size,
                         hashlib.sha256(new_wheel.read_bytes()).hexdigest())
        try:
            services.start()
            services.start(worker=True)
            # Verify the old instance before beginning the actual transaction.
            import requests

            for _attempt in range(100):
                try:
                    if requests.get(profile.values['health_url'], timeout=1).json().get('version') == '0.0.1':
                        break
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError('The disposable old instance did not start.')
            with patch('copydesk.update_commands.server_controller', return_value=controller):
                result = CliRunner().invoke(cli, ['update', 'bootstrap', '--profile', str(profile_path),
                                                  '--wheel', str(new_wheel), '--sha256', release.sha256, '--yes'])
            assert result.exit_code == 0, result.output + repr(result.exception)
            print(result.output)
            state = controller.state()
            assert state['stage'] == 'complete' and services.version() == '0.0.2'
            assert publication_file.read_text() == 'original private upload'
            assert requests.get(profile.values['health_url'], timeout=5).json()['maintenance'] is False
            publication_file.write_text('content received after update')
            rollback = controller.rollback()
            assert rollback['stage'] == 'rolled_back' and services.version() == '0.0.1'
            assert publication_file.read_text() == 'original private upload'
            safety = Path(rollback['transaction']) / 'snapshot/instance/data/uploads/synthetic.txt'
            assert safety.read_text() == 'content received after update'
            # Failed candidate health must restore both code and database.
            controller.health = lambda version: False
            controller.client = LocalPackages(new_wheel)
            controller.profile.values['health_timeout'] = 0.1
            try:
                controller.execute(release)
            except UpdateError as error:
                assert 'health check' in str(error)
            else:
                raise AssertionError('A failed health check was accepted.')
            assert controller.state()['stage'] == 'rolled_back' and services.version() == '0.0.1'
            assert publication_file.read_text() == 'original private upload'
            print('Real server preparation, migration, HTTP/worker validation, manual rollback and failed-update recovery passed.')
        finally:
            services.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    main(parser.parse_args().wheel.resolve())
