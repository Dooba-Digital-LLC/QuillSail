"""Local desktop lifecycle; no GUI imports or operator environment overrides.

The launcher owns one private instance, one loopback HTTP server and one worker.
Only a short-lived, single-use launch ticket authorizes the browser setup form.
It never logs a user in or creates an administrator by itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from urllib.parse import urlencode, urlsplit

from flask import abort, redirect, request, session, url_for
from sqlalchemy import select
from werkzeug.serving import WSGIRequestHandler, make_server
from werkzeug.wrappers import Response

from copydesk import __version__, migrate
from copydesk.app import create_app
from copydesk.config import ConfigError, load_config
from copydesk.db import db, make_engine
from copydesk.jobs import Worker
from copydesk.models import User

FORMAT = "quillsail.desktop.v1"


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/QuillSail"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "QuillSail"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "quillsail"


def write_private(path: Path, content: str) -> None:
    """Replace a local state file atomically, without briefly exposing secrets."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    """An OS lock is released on crashes; a stale file never blocks a restart."""

    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def acquire(self):
        self.handle = self.path.open("a+b")
        os.chmod(self.path, 0o600)
        if self.path.stat().st_size == 0:
            self.handle.write(b"\0")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise AlreadyRunning("QuillSail is already running for this publication. Use its existing launcher window.") from exc

    def release(self):
        if self.handle:
            self.handle.close()
            self.handle = None


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        # Launch tickets and private draft/subscriber URLs must not enter logs.
        pass


class LocalRequests:
    """Reject other network clients, DNS rebinding and cross-origin requests."""

    def __init__(self, app, origin: str):
        self.app, self.origin = app, origin
        self.host = urlsplit(origin).netloc

    def __call__(self, environ, start_response):
        if environ.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"} or environ.get("HTTP_HOST") != self.host:
            return Response("This publication is available only on this computer.", status=403)(environ, start_response)
        if environ.get("HTTP_ORIGIN") not in {None, self.origin}:
            return Response("Open QuillSail from its desktop launcher.", status=403)(environ, start_response)
        return self.app(environ, start_response)


class DesktopRuntime:
    def __init__(self, directory: Path | None = None):
        self.root = (directory or default_data_dir()).expanduser().resolve()
        self.config_path = self.root / "copydesk.toml"
        self.state_path = self.root / "desktop.json"
        self.lock = InstanceLock(self.root / "desktop.lock")
        self.app = self.server = self.worker = None
        self.http_thread = self.worker_thread = None
        self.url = ""
        self._proof = secrets.token_urlsafe(32)
        self._tickets: dict[str, float] = {}
        self._ticket_lock = threading.Lock()

    def _initialize(self):
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("format") != FORMAT or not self.config_path.is_file():
                raise ConfigError("This folder is not a complete QuillSail desktop publication.")
        else:
            if any(path.name not in {"desktop.lock", "quillsail.log", "quillsail.log.1", "quillsail.log.2"} for path in self.root.iterdir()):
                raise ConfigError("This folder already contains other files. Choose an empty folder for the desktop publication.")
            template = (Path(__file__).parent / "config_template.toml").read_text(encoding="utf-8")
            write_private(self.config_path, Template(template).substitute(
                base_url="http://127.0.0.1:5080", secret_key=secrets.token_hex(32),
                database_url="sqlite:///data/copydesk.db", mail_backend="file",
                from_email="newsletter@localhost", proxy_hops="0"))
            state = {"format": FORMAT, "port": 5080}
            write_private(self.state_path, json.dumps(state) + "\n")
        config = load_config(self.config_path, require_file=True, use_environment=False)
        engine = make_engine(config.database_url)
        try:
            if engine.dialect.name != "sqlite" or not Path(engine.url.database or "").resolve().is_relative_to(self.root):
                raise ConfigError("Desktop publications use a SQLite database inside their own data folder.")
            database = Path(engine.url.database)
            for path in (config.data_dir, config.uploads_dir, config.themes_dir, config.extensions_dir, config.mail.file_dir):
                if not path.resolve().is_relative_to(self.root):
                    raise ConfigError("Desktop publication files must stay inside their own data folder.")
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
            database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if migrate.needs_upgrade(engine):
                if database.exists() and migrate.current_revision(engine) is not None:
                    self._backup(database, config)
                migrate.upgrade(engine)
            os.chmod(database, 0o600)
        finally:
            engine.dispose()
        # The desktop never inherits a proxy, debug server or web worker setting.
        config.proxy_hops, config.debug, config.run_jobs_in_web = 0, False, False
        return config, state

    def _backup(self, database, config):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.root / "backups" / f"{stamp}-before-{__version__}"
        backup.mkdir(parents=True, mode=0o700)
        with sqlite3.connect(database) as source, sqlite3.connect(backup / "copydesk.db") as destination:
            source.backup(destination)
        shutil.copyfile(self.config_path, backup / "copydesk.toml")
        with tarfile.open(backup / "files.tar.gz", "w:gz") as archive:
            for name, path in (("uploads", config.uploads_dir), ("themes", config.themes_dir),
                               ("extensions", config.extensions_dir), ("mail", config.mail.file_dir)):
                archive.add(path, arcname=name)
        for path in backup.iterdir():
            os.chmod(path, 0o600)

    def start(self, *, with_worker=True):
        if self.server is not None:
            raise RuntimeError("This publication is already running.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock.acquire()
        try:
            config, saved = self._initialize()
            self.app = create_app(config)
            self.app.config["SESSION_COOKIE_NAME"] = "quillsail_desktop_" + hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
            self.app.extensions["copydesk_desktop"] = self
            self.app.add_url_rule("/_desktop/open", "setup.desktop_open", self._open)
            port = saved.get("port", 5080)
            if not isinstance(port, int) or not 0 <= port <= 65535:
                port = 0
            try:
                self.server = make_server("127.0.0.1", port, self.app, threaded=True, request_handler=QuietHandler)
            except (OSError, SystemExit):
                self.server = make_server("127.0.0.1", 0, self.app, threaded=True, request_handler=QuietHandler)
            self.url = f"http://127.0.0.1:{self.server.server_port}"
            config.base_url = self.url
            self.app.config["SERVER_NAME"] = urlsplit(self.url).netloc
            self.app.wsgi_app = LocalRequests(self.app.wsgi_app, self.url)
            write_private(self.state_path, json.dumps({"format": FORMAT, "port": self.server.server_port, "version": __version__}) + "\n")
            self.http_thread = threading.Thread(target=self.server.serve_forever, name="quillsail-local-web", daemon=True)
            self.http_thread.start()
            if with_worker:
                self.worker = Worker(self.app)
                self.worker_thread = threading.Thread(target=self.worker.run, name="quillsail-local-worker", daemon=True)
                self.worker_thread.start()
        except BaseException:
            self.stop()
            raise
        return self.url

    def launch_url(self) -> str:
        if self.server is None:
            raise RuntimeError("Start QuillSail first.")
        ticket = secrets.token_urlsafe(32)
        with self._ticket_lock:
            now = time.monotonic()
            self._tickets = {key: expiry for key, expiry in self._tickets.items() if expiry > now}
            self._tickets[ticket] = now + 90
        return self.url + "/_desktop/open?" + urlencode({"ticket": ticket})

    def _open(self):
        ticket = request.args.get("ticket", "")
        with self._ticket_lock:
            expiry = self._tickets.pop(ticket, 0)
        if expiry <= time.monotonic():
            abort(403)
        with self.app.app_context():
            has_users = db.session.scalar(select(User.id).limit(1)) is not None
        if not has_users:
            session["_desktop_setup"] = self._proof
        response = redirect(url_for("dash.overview") if has_users else url_for("setup.index"))
        response.headers.update({"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})
        return response

    def setup_authorized(self):
        return hmac.compare_digest(str(session.get("_desktop_setup", "")), self._proof)

    def stop(self):
        try:
            if self.worker:
                self.worker.stop.set()
            if self.worker_thread:
                self.worker_thread.join()
            if self.server:
                if self.http_thread and self.http_thread.is_alive():
                    self.server.shutdown()
                    self.http_thread.join()
                self.server.server_close()
            if self.app:
                with self.app.app_context():
                    db.session.remove()
                    db.engine.dispose()
        finally:
            self.server = self.worker = self.app = None
            self.http_thread = self.worker_thread = None
            self._proof = secrets.token_urlsafe(32)
            self._tickets.clear()
            self.lock.release()


def smoke_test():
    """Exercise the frozen application's migrations, browser setup and restart."""
    import re

    import requests
    from cryptography.fernet import Fernet

    cipher = Fernet(Fernet.generate_key())
    assert cipher.decrypt(cipher.encrypt(b"local-packaging-check")) == b"local-packaging-check"
    with tempfile.TemporaryDirectory(prefix="quillsail-desktop-smoke-") as directory:
        runtime = DesktopRuntime(Path(directory))
        try:
            runtime.start(with_worker=False)
            with requests.Session() as browser:
                browser.trust_env = False
                setup = browser.get(runtime.launch_url(), timeout=15)
                assert setup.status_code == 200 and 'name="code"' not in setup.text
                csrf = re.search(r'name="csrf_token" value="([^"]+)"', setup.text)[1]
                dashboard = browser.post(runtime.url + "/setup/", data={"csrf_token": csrf, "site_name": "Local smoke publication",
                    "site_mode": "hybrid", "display_name": "Local Owner", "email": "owner@example.test",
                    "password": "synthetic-local-smoke-password-123"}, timeout=15)
                assert dashboard.status_code == 200 and dashboard.url == runtime.url + "/dashboard/"
                assert browser.get(runtime.url + "/static/css/copydesk.css", timeout=15).status_code == 200
                secret = load_config(runtime.config_path, use_environment=False).secret_key
                runtime.stop()
                runtime.start(with_worker=False)
                assert load_config(runtime.config_path, use_environment=False).secret_key == secret
                assert browser.get(runtime.launch_url(), timeout=15).url == runtime.url + "/dashboard/"
                assert browser.get(runtime.url + "/healthz", timeout=15).json()["database"] == "ok"
        finally:
            runtime.stop()
    print(json.dumps({"desktop_smoke": "passed", "setup_and_dashboard": True, "restart_preserves_data": True, "crypto_roundtrip": True, "sent_emails": 0}))
