"""Verified, on-demand cloudflared download and a quiet supervised process."""
from __future__ import annotations

import hashlib
import io
import os
import platform
import socket
import subprocess
import tarfile
import threading
from pathlib import Path

import requests

from copydesk.domains import DomainProblem

VERSION = '2026.10.7'
# Official release asset digests; checked before extracting or executing anything.
PINS = {
    ('Darwin', 'arm64'): ('cloudflared-darwin-arm64.tgz', '3586ccf397e98e5f437e60e7c307ca6ac463e110580503af8bb8e6f9ebf831bd'),
    ('Darwin', 'x86_64'): ('cloudflared-darwin-amd64.tgz', '02f3edfd87684dd5b705607688e4eb4b323f6e777b81e7b3a85af556aa356848'),
    ('Windows', 'AMD64'): ('cloudflared-windows-amd64.exe', '2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343'),
    ('Linux', 'x86_64'): ('cloudflared-linux-amd64', '1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872'),
}


def binary(directory):
    from copydesk.desktop import write_private

    pair = PINS.get((platform.system(), platform.machine()))
    if not pair:
        raise DomainProblem('Automatic tunnel downloads are not available for this platform.')
    name, digest = pair
    directory = Path(directory) / 'cloudflared' / VERSION
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = directory / name
    try:
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{name}', timeout=(5, 60), stream=True)
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(1024 * 1024):
                    content.extend(chunk)
                    if len(content) > 100 * 1024 * 1024:
                        raise DomainProblem('The tunnel download is larger than expected.')
            if hashlib.sha256(content).hexdigest() != digest:
                raise DomainProblem('The tunnel download checksum did not match. Nothing was executed.')
            # No executable file exists before the whole download is verified.
            import tempfile

            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
                temp = Path(handle.name)
                os.chmod(temp, 0o600)
                handle.write(content)
            os.replace(temp, archive)
        content = archive.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise DomainProblem('The tunnel download checksum did not match.')
        if name.endswith('.tgz'):
            with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as bundle:
                member = bundle.getmember('cloudflared')
                if not member.isfile() or member.size > 100 * 1024 * 1024:
                    raise DomainProblem('The tunnel archive is invalid.')
                content = bundle.extractfile(member).read()
        executable = directory / ('cloudflared.exe' if platform.system() == 'Windows' else 'cloudflared')
        # Recreate from verified archive even if an old extracted binary was altered.
        import tempfile

        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            temp = Path(handle.name)
            os.chmod(temp, 0o600)
            handle.write(content)
        os.replace(temp, executable)
        executable.chmod(0o700)
        write_private(directory / 'LICENSE.txt', 'cloudflared — Copyright Cloudflare, Inc. Licensed under Apache-2.0.\nhttps://github.com/cloudflare/cloudflared/blob/' + VERSION + '/LICENSE\n')
        return executable
    except (requests.RequestException, OSError, tarfile.TarError, KeyError):
        raise DomainProblem('The verified tunnel download could not be prepared. Please retry.') from None


class Tunnel:
    def __init__(self, executable, token):
        self.executable, self._token = str(executable), token
        self.process = self.thread = None
        self.stop_event = threading.Event()
        self.status = 'Connecting'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]

    def ready(self):
        try:
            with requests.Session() as session:
                session.trust_env = False
                return session.get(f'http://127.0.0.1:{self.port}/ready', timeout=2).status_code == 200
        except requests.RequestException:
            return False

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name='quillsail-tunnel', daemon=True)
        self.thread.start()

    def _run(self):
        delay = 1
        while not self.stop_event.is_set():
            self.status = 'Connecting'
            try:
                self.process = subprocess.Popen([
                    self.executable, 'tunnel', '--no-autoupdate', '--metrics', f'127.0.0.1:{self.port}', 'run'],
                    env={**os.environ, 'TUNNEL_TOKEN': self._token},
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                while not self.stop_event.wait(2) and self.process.poll() is None:
                    self.status = 'Online' if self.ready() else 'Connecting'
                    if self.status == 'Online':
                        delay = 1
            except OSError:
                pass
            finally:
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
                self.process = None
            self.status = 'Offline'
            if self.stop_event.wait(delay):
                break
            delay = min(60, delay * 2)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        self.status = 'Offline'
        self._token = ''


class AwakeGuard:
    """Opt-in idle-sleep inhibition; platform resources belong to one thread."""
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.problem = ''

    def start(self):
        self.thread = threading.Thread(target=self._run, name='quillsail-awake', daemon=True)
        self.thread.start()

    def _run(self):
        if platform.system() == 'Windows':
            import ctypes

            function = ctypes.windll.kernel32.SetThreadExecutionState
            if not function(0x80000001):
                self.problem = 'Windows could not keep this computer awake.'
                return
            try:
                self.stop_event.wait()
            finally:
                function(0x80000000)
            return
        import shutil

        command = (['caffeinate', '-i', '/bin/cat'] if platform.system() == 'Darwin' else
                   ['systemd-inhibit', '--what=idle:sleep', '--mode=block', '--why=QuillSail publication is public', '/bin/cat'])
        if not shutil.which(command[0]):
            self.problem = 'Sleep inhibition is unavailable on this computer.'
            return
        process = None
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while not self.stop_event.wait(1):
                if process.poll() is not None:
                    self.problem = 'The computer could not be kept awake. Check its power settings.'
                    break
        except OSError:
            self.problem = 'Sleep inhibition could not be started.'
        finally:
            if process:
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
