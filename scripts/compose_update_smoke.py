"""Run the managed Compose updater against disposable containers and file mail."""

import hashlib
import json
import os
import socket
import sys
import tempfile
import time
import uuid
from pathlib import Path

import requests
from server_update_smoke import LocalPackages, synthetic_wheel

from copydesk.compose_update import ComposeDeployment
from copydesk.server_update import CommandRunner, ServerProfile, ServerUpdater, private_json
from copydesk.updater import Update


def main(source):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise SystemExit('Run this disposable Docker test as root on Linux.')
    run = CommandRunner()
    run(['docker', 'pull', 'python:3.13-slim'])
    base = json.loads(run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', 'python:3.13-slim']))[0]
    project = 'quillsail-smoke-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='quillsail-compose-smoke-') as folder:
        root = Path(folder)
        instance, application, backups = root / 'instance', root / 'application', root / 'backups'
        for path in (instance, application, backups):
            path.mkdir()
        os.chown(instance, 10001, 10001)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        old_wheel = synthetic_wheel(source, root, '0.0.1')
        new_wheel = synthetic_wheel(source, root, '0.0.2')
        context = root / 'initial-build'
        context.mkdir()
        (context / old_wheel.name).write_bytes(old_wheel.read_bytes())
        (context / 'Dockerfile').write_text('\n'.join([
            'FROM ' + base,
            f'COPY {old_wheel.name} /tmp/',
            f'RUN python -m pip install --quiet "/tmp/{old_wheel.name}[server]"',
            'USER 10001:10001', 'ENTRYPOINT ["python", "-m", "copydesk.container"]', '']))
        old_image = project + ':initial'
        run(['docker', 'build', '-t', old_image, context], timeout=1800)
        services = {}
        for name in ('web', 'worker', 'init'):
            services[name] = {'image': old_image, 'command': [name], 'volumes': [f'{instance}:/var/lib/copydesk'],
                              'environment': {'COPYDESK_CONFIG': '/var/lib/copydesk/copydesk.toml',
                                              'COPYDESK_BASE_URL': f'http://127.0.0.1:{port}/publication'},
                              'stop_grace_period': '20s'}
        services['web']['ports'] = [f'127.0.0.1:{port}:8000']
        compose = application / 'managed-compose.json'
        private_json(compose, {'services': services})
        profile_path = root / 'update.json'
        private_json(profile_path, {'format': 'quillsail.server-update.v1', 'kind': 'compose',
                     'install_root': str(application), 'instance_root': str(instance), 'config': str(instance / 'copydesk.toml'),
                     'backup_root': str(backups), 'compose_file': str(compose), 'compose_project': project,
                     'base_image': base, 'health_url': f'http://127.0.0.1:{port}/publication/healthz', 'health_timeout': 45})
        profile = ServerProfile.load(profile_path)
        deployment = ComposeDeployment(profile, run)
        try:
            deployment.migrate()
            deployment.start()
            deployment.start(worker=True)
            for _attempt in range(100):
                try:
                    if requests.get(profile.values['health_url'], timeout=1).json().get('version') == '0.0.1':
                        break
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(0.2)
            else:
                raise AssertionError('The disposable initial container did not start.')
            controller = ServerUpdater(profile, run=run, deployment=deployment, client=LocalPackages(new_wheel))
            controller.report = print
            release = Update('0.0.1', '0.0.2', 'server', 'offline', new_wheel.name, new_wheel.stat().st_size,
                             hashlib.sha256(new_wheel.read_bytes()).hexdigest())
            state = controller.execute(release)
            assert state['stage'] == 'complete' and deployment.version() == '0.0.2'
            assert requests.get(profile.values['health_url'], timeout=5).json()['maintenance'] is False
            controller.rollback()
            assert deployment.version() == '0.0.1'
            print('Real Compose image preparation, migration, version/worker checks and matching rollback passed.')
        finally:
            deployment.command('down', '--remove-orphans')


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
