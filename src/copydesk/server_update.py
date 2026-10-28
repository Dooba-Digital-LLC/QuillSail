"""Transactional updates for registered Python/systemd and Compose servers.

Release preparation happens online. Database and instance snapshots happen
only after services stop. The maintenance gate keeps validation reversible;
once committed, recovery never restores a snapshot over new reader/mail work.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from sqlalchemy import select

from copydesk.config import load_config
from copydesk.desktop import AlreadyRunning, InstanceLock, write_private
from copydesk.maintenance import marker
from copydesk.models import Setting
from copydesk.server_backup import DatabaseBackup
from copydesk.updater import UpdateClient, UpdateError

DEFAULT_PROFILE = Path("/etc/copydesk/update.json")
PROTOCOL = 1
TERMINAL = {"complete", "rolled_back", "cancelled"}
REVERSIBLE = {"backup_complete", "migrating", "activating", "validating", "rolling_back"}


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(path, json.dumps(value, indent=2) + "\n")
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def read_json(path):
    if path.stat().st_size > 2_000_000:
        raise UpdateError("The update state file is too large.")
    return json.loads(path.read_text(encoding="utf-8"))


def trusted_file(path):
    """A privileged updater never accepts a service-writable operation plan."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_uid != os.geteuid():
        raise UpdateError("Update profiles must be owned by the invoking operator and must not be writable by other users.")


def trusted_directory(path):
    """Deployment code and recovery plans must not be service-writable."""
    path = path.resolve()
    for current in [path, *path.parents]:
        if not current.exists():
            continue
        info = current.stat()
        sticky_shared_parent = bool(info.st_mode & stat.S_ISVTX) and info.st_uid == 0
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or (info.st_mode & 0o022 and not sticky_shared_parent)):
            raise UpdateError("Application and backup folders must be operator-owned and protected from other users.")


UNPROTECTED_RELEASE = ("Existing application releases must be operator-owned and protected from service-user "
                       "changes before privileged updates.")


def trusted_code(path):
    """A file or folder that root will run, such as a release's base interpreter."""
    info = path.stat()
    if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
        raise UpdateError(UNPROTECTED_RELEASE)
    trusted_directory(path if stat.S_ISDIR(info.st_mode) else path.parent)


@dataclass
class ServerProfile:
    path: Path
    values: dict

    @classmethod
    def load(cls, path):
        path = Path(path).expanduser().absolute()
        trusted_file(path)
        values = read_json(path)
        if values.get("format") != "quillsail.server-update.v1" or values.get("kind") not in {"python", "compose"}:
            raise UpdateError("This is not a supported QuillSail server update profile.")
        profile = cls(path, values)
        profile.validate()
        return profile

    def at(self, name):
        return Path(self.values[name]).expanduser().resolve()

    @property
    def kind(self):
        return self.values["kind"]

    def validate(self):
        trusted_directory(self.path.parent)
        for key in ("instance_root", "config", "backup_root", "install_root"):
            if not isinstance(self.values.get(key), str) or not Path(self.values[key]).is_absolute():
                raise UpdateError(f"The server profile needs an absolute {key} path.")
        instance, install, backup = (self.at(key) for key in ("instance_root", "install_root", "backup_root"))
        if (instance == Path("/") or install == Path("/") or backup == Path("/")
                or install.is_relative_to(instance) or instance.is_relative_to(install)
                or backup.is_relative_to(instance) or instance.is_relative_to(backup)
                or self.path.resolve().is_relative_to(instance)):
            raise UpdateError("Keep application releases, publication files, update profiles and backups in separate folders.")
        if not self.at("config").is_relative_to(instance):
            raise UpdateError("The instance configuration must be inside the approved instance folder.")
        url = urlsplit(self.values.get("health_url", ""))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise UpdateError("Configure an HTTP(S) health URL without embedded credentials.")
        if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise UpdateError("Public health URLs must use HTTPS.")
        trusted_directory(install)
        if self.kind == "python":
            for key in ("web_service", "worker_service"):
                value = self.values.get(key, "")
                if not value or not re.fullmatch(r"[A-Za-z0-9@_.-]+", value):
                    raise UpdateError("Specify valid systemd service names in the server profile.")
            if not isinstance(self.values.get("service_user"), str):
                raise UpdateError("Specify the systemd service user in the profile.")
        else:
            for key in ("compose_file", "compose_project", "base_image"):
                if not self.values.get(key):
                    raise UpdateError(f"The Compose profile needs {key}.")
            if not re.fullmatch(r"[A-Za-z0-9_./:-]+@sha256:[a-f0-9]{64}", self.values["base_image"]):
                raise UpdateError("Pin the Compose build's Python base image by SHA-256 digest.")
        trusted_directory(backup)

    def environment(self):
        from dotenv import dotenv_values

        values = {}
        for filename in self.values.get("environment_files", []):
            path = Path(filename)
            trusted_file(path)
            values.update({key: value for key, value in dotenv_values(path, interpolate=False).items() if value is not None})
        if self.kind == "compose":
            values.update(read_json(self.at("compose_file"))["services"]["web"].get("environment", {}))
        return values

    def config(self):
        from copydesk.config import ENV_OVERRIDES

        overrides = {}
        for key, value in self.environment().items():
            if key in ENV_OVERRIDES:
                section, name = ENV_OVERRIDES[key]
                overrides.setdefault(section, {})[name] = value
        config = load_config(self.at("config"), overrides=overrides, require_file=True, use_environment=False)
        if self.kind == "compose":
            from dataclasses import replace

            from sqlalchemy.engine import make_url

            container_root = Path(self.values.get("container_root", "/var/lib/copydesk"))
            def remap(path):
                path = Path(path)
                if path.is_relative_to(container_root):
                    return self.at("instance_root") / path.relative_to(container_root)
                return path
            url = make_url(config.database_url)
            if url.get_backend_name() == "sqlite":
                url = url.set(database=str(remap(url.database)))
            config = replace(config, data_dir=remap(config.data_dir), uploads_dir=remap(config.uploads_dir),
                             themes_dir=remap(config.themes_dir), extensions_dir=remap(config.extensions_dir),
                             database_url=url.render_as_string(hide_password=False), mail=replace(config.mail, file_dir=remap(config.mail.file_dir)))
        for path in (config.data_dir, config.uploads_dir, config.themes_dir, config.extensions_dir, config.mail.file_dir):
            if not path.resolve().is_relative_to(self.at("instance_root")):
                raise UpdateError("All mutable publication paths must stay inside the approved instance folder.")
        from sqlalchemy.engine import make_url

        database = make_url(config.database_url)
        if database.get_backend_name() == "sqlite" and (not database.database or not Path(database.database).resolve().is_relative_to(self.at("instance_root"))):
            raise UpdateError("The SQLite database must stay inside the approved instance folder.")
        if self.values.get("backup_host"):
            from dataclasses import replace

            from sqlalchemy.engine import make_url

            url = make_url(config.database_url).set(host=self.values["backup_host"], port=self.values.get("backup_port"))
            config = replace(config, database_url=url.render_as_string(hide_password=False))
        return config


class CommandRunner:
    log_path = None

    def __call__(self, arguments, *, env=None, stdout=None, stdin=None, timeout=900):
        # Python puts the working directory first on sys.path for -c and -m.
        # Root must never import modules from an operator's (possibly
        # service-writable) folder, so every command runs from /.
        try:
            result = subprocess.run([str(arg) for arg in arguments], env=runtime_environment() if env is None else env, stdin=stdin,
                                    stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, cwd="/")
        except subprocess.TimeoutExpired as exc:
            raise UpdateError(f"{Path(arguments[0]).name} timed out.") from exc
        if self.log_path:
            fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "ab") as handle:
                handle.write(("\n" + Path(arguments[0]).name + "\n").encode())
                if result.stdout:
                    handle.write(result.stdout)
                handle.write(result.stderr or b"")
        if result.returncode:
            # External utilities may include credentials in their error output.
            # Keep diagnostics in the protected transaction log, never CLI output.
            raise UpdateError(f"{Path(arguments[0]).name} failed with exit status {result.returncode}.")
        return result.stdout.decode("utf-8", errors="replace") if result.stdout else ""


def runtime_environment():
    """Candidate processes must not inherit a source checkout or another instance."""
    return {key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("COPYDESK_")}


class PythonDeployment:
    def __init__(self, profile, run):
        self.profile, self.run = profile, run
        self.current = profile.at("install_root") / "current"

    def systemctl(self, *arguments):
        return self.run(["systemctl", *arguments])

    def version(self):
        self.check_release()
        return self.run([self.current / "venv/bin/python", "-c", "import importlib.metadata; print(importlib.metadata.version('copydesk'))"]).strip()

    def check_release(self):
        root = self.profile.at("install_root")
        if not self.current.is_symlink() or not self.current.resolve().is_relative_to(root / "releases"):
            raise UpdateError("Register a versioned server installation with current pointing into its releases folder.")
        # Merely asking a Python environment for its package version executes
        # .pth/site code. Never run a service-writable environment as root.
        trusted_directory(self.current.resolve())
        for base, directories, files in os.walk(self.current.resolve(), followlinks=False):
            trusted_directory(Path(base))
            for name in directories + files:
                path = Path(base) / name
                if path.is_symlink():
                    # For example venv/bin/python -> the base interpreter.
                    trusted_code(path.resolve())
                elif (info := path.lstat()).st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                    raise UpdateError(UNPROTECTED_RELEASE)

    def preflight(self):
        self.check_release()
        for key in ("web_service", "worker_service"):
            unit = self.profile.values.get(key)
            if not unit:
                continue
            user = self.systemctl("show", unit, "--property=User", "--value").strip()
            if user != self.profile.values["service_user"]:
                raise UpdateError("The configured services do not run as the approved service user.")
            command = self.systemctl("show", unit, "--property=ExecStart", "--value")
            if str(self.current / "venv/bin/") not in command:
                raise UpdateError("The systemd services must use the registered current/venv installation.")

    def prepare(self, wheel, version, transaction):
        release = self.profile.at("install_root") / "releases" / (transaction.name + "-" + version)
        release.mkdir(parents=True, mode=0o755)
        self.run([self.current / "venv/bin/python", "-m", "venv", release / "venv"])
        config = self.profile.config()
        from sqlalchemy.engine import make_url

        backend = make_url(config.database_url).get_backend_name()
        extras = ["server", *( ["postgres"] if backend == "postgresql" else ["mysql"] if backend == "mysql" else [])]
        self.run([release / "venv/bin/python", "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
                  str(wheel) + "[" + ",".join(extras) + "]", *self.profile.values.get("requirements", [])])
        capabilities = json.loads(self.run([release / "venv/bin/quillsail", "update", "capabilities"]))
        if capabilities.get("server_update_protocol") != PROTOCOL or not capabilities.get("maintenance_gate") or capabilities.get("version") != version:
            raise UpdateError("The candidate package does not support safe server-update validation.")
        return {"old_release": str(self.current.resolve()), "new_release": str(release)}

    def activate(self, state, *, previous=False):
        destination = state["old_release" if previous else "new_release"]
        root = self.profile.at("install_root")
        path = Path(destination).resolve()
        if not path.is_relative_to(root / "releases") or not (path / "venv/bin/python").is_file():
            raise UpdateError("The release selected for activation is invalid.")
        temporary = root / (".current-" + uuid.uuid4().hex)
        temporary.symlink_to(path, target_is_directory=True)
        os.replace(temporary, self.current)
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def stop(self):
        units = [self.profile.values[key] for key in ("worker_service", "web_service") if self.profile.values.get(key)]
        self.systemctl("stop", *units)

    def start(self, *, worker=False):
        key = "worker_service" if worker else "web_service"
        if unit := self.profile.values.get(key):
            self.systemctl("start", unit)

    def migrate(self):
        self.as_service([self.current / "venv/bin/quillsail", "--config", self.profile.at("config"), "upgrade"])

    def as_service(self, arguments):
        user = self.profile.values["service_user"]
        environment = {**runtime_environment(), **self.profile.environment()}
        if os.geteuid() == 0 and user != "root":
            return self.run(["runuser", "-u", user, "--", *arguments], env=environment)
        return self.run(arguments, env=environment)

    def worker_active(self):
        if not (unit := self.profile.values.get("worker_service")):
            return True
        try:
            return self.systemctl("is-active", unit).strip() == "active"
        except UpdateError:
            return False

    def ownership(self):
        import pwd

        user = pwd.getpwnam(self.profile.values["service_user"])
        return user.pw_uid, user.pw_gid


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_instance(source, destination):
    """Never follow links or preserve device nodes in an instance snapshot."""
    for base, directories, files in os.walk(source, followlinks=False):
        for name in directories + files:
            path = Path(base) / name
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise UpdateError("Instance snapshots require ordinary files and folders; remove filesystem links or special files before updating.")
    shutil.copytree(source, destination, symlinks=False)


class ServerUpdater:
    def __init__(self, profile, *, client=None, run=None, deployment=None, require_privileges=True, health=None):
        self.profile = profile
        self.run = run or CommandRunner()
        profile.at("backup_root").mkdir(parents=True, exist_ok=True, mode=0o700)
        trusted_directory(profile.at("backup_root"))
        self.client = client or UpdateClient(cache=profile.at("backup_root") / "metadata-cache")
        self.config = profile.config()
        if deployment is None and profile.kind == "compose":
            from copydesk.compose_update import ComposeDeployment

            deployment = ComposeDeployment(profile, self.run)
        self.deployment = deployment or PythonDeployment(profile, self.run)
        self.require_privileges = require_privileges
        self.health = health or self._health
        self.report = lambda message: None
        self.journal_path = profile.at("backup_root") / "latest.json"

    @contextlib.contextmanager
    def lock(self):
        # Lock both the deployment and recovery store. Two profiles must never
        # update the same application using different backup directories.
        locks = []
        try:
            for path in sorted({self.profile.at("install_root") / "update.lock", self.profile.at("backup_root") / "update.lock"}):
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                lock = InstanceLock(path)
                try:
                    lock.acquire()
                except AlreadyRunning as exc:
                    raise UpdateError("Another server update is already running.") from exc
                locks.append(lock)
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    def check_space(self, update, database):
        size = sum(path.stat().st_size for path in self.profile.at("instance_root").rglob("*") if path.is_file() and not path.is_symlink())
        database_size = database.estimated_size()
        for folder, required in ((self.profile.at("backup_root"), (size + database_size) * 2 + update.length * 2),
                                 (self.profile.at("install_root"), max(200_000_000, update.length * 10)),
                                 (self.profile.at("instance_root").parent, size * 2)):
            if shutil.disk_usage(folder).free < required:
                raise UpdateError("There is not enough free disk space for release preparation and recovery snapshots.")

    def _health(self, version):
        response = requests.get(self.profile.values["health_url"], timeout=5,
                                headers={"Cache-Control": "no-cache"}, allow_redirects=False)
        data = response.json()
        return response.status_code == 200 and data.get("version") == version and data.get("database") == "ok" and data.get("maintenance") is True

    def wait_health(self, version):
        deadline = time.monotonic() + self.profile.values.get("health_timeout", 60)
        while time.monotonic() < deadline:
            try:
                if self.health(version):
                    return
            except (requests.RequestException, ValueError):
                pass
            time.sleep(0.5)
        raise UpdateError("The application did not pass its post-update health check.")

    def state(self):
        return read_json(self.journal_path) if self.journal_path.exists() else None

    def save(self, state, stage):
        candidate = {**state, "stage": stage, "updated_at": datetime.now(UTC).isoformat()}
        private_json(Path(state["transaction"]) / "journal.json", candidate)
        private_json(self.journal_path, candidate)
        state.update(candidate)
        self.report({"preparing": "Preparing the verified release; the site stays online.",
                     "stopping": "Entering maintenance mode and stopping services.",
                     "backup_complete": "The database and complete instance backup are verified.",
                     "migrating": "Applying database migrations.", "validating": "Checking the new web service and paused worker.",
                     "committed": "Committing the healthy deployment.", "complete": "Update complete; the site and worker are online.",
                     "rolling_back": "Restoring the matching application and instance backup.",
                     "rolled_back": "The previous deployment has been restored."}.get(stage, "Update state: " + stage))

    def _flag(self, state):
        path = marker(self.config)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and read_json(path).get("transaction") != state["transaction"]:
            raise UpdateError("Another maintenance operation owns the publication gate.")
        private_json(path, {"transaction": state["transaction"]})

    def _unflag(self, state):
        path = marker(self.config)
        if path.exists():
            if read_json(path).get("transaction") != state["transaction"]:
                raise UpdateError("The maintenance gate belongs to another operation.")
            path.unlink()

    def _backup(self, state, database):
        folder = Path(state["transaction"]) / "snapshot"
        folder.mkdir(mode=0o700)
        copy_instance(self.profile.at("instance_root"), folder / "instance")
        database.snapshot(folder)
        for path in folder.rglob("*"):
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        files = {str(path.relative_to(folder)): sha256(path) for path in folder.rglob("*") if path.is_file()}
        private_json(folder / "manifest.json", {"files": files})

    def _restore(self, state, database):
        folder = Path(state.get("snapshot_source", str(Path(state["transaction"]) / "snapshot")))
        if not folder.resolve().is_relative_to(self.profile.at("backup_root")):
            raise UpdateError("The recovery snapshot is outside the approved backup folder.")
        manifest = read_json(folder / "manifest.json")
        for name, digest in manifest["files"].items():
            path = folder / name
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or sha256(path) != digest:
                raise UpdateError("The instance backup failed verification. Services remain stopped for recovery.")
        root = self.profile.at("instance_root")
        restored = root.parent / (".quillsail-restore-" + uuid.uuid4().hex)
        copy_instance(folder / "instance", restored)
        uid, gid = self.deployment.ownership()
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, uid, gid)
        displaced = root.parent / (".quillsail-displaced-" + uuid.uuid4().hex)
        os.replace(root, displaced)
        try:
            os.replace(restored, root)
        except OSError:
            os.replace(displaced, root)
            raise
        database.restore(folder)
        marker(self.config).unlink(missing_ok=True)
        self._flag(state)
        if database.kind == "sqlite":
            os.chown(database.url.database, uid, gid)

    def _worker_identity(self, database):
        with database.engine.connect() as connection:
            return connection.execute(select(Setting.value).where(Setting.key == "system.worker_identity")).scalar()

    def _validate_worker(self, database, previous):
        deadline = time.monotonic() + self.profile.values.get("health_timeout", 60)
        while time.monotonic() < deadline:
            if not marker(self.config).exists():
                raise UpdateError("The maintenance gate disappeared during validation.")
            identity = self._worker_identity(database)
            if self.deployment.worker_active() and identity and identity != previous:
                return
            time.sleep(0.5)
        raise UpdateError("The paused worker did not report a fresh heartbeat.")

    def _recover(self, state, database):
        stage = state["stage"]
        if stage in {"preparing", "prepared"}:
            self._unflag(state)
            self.save(state, "cancelled")
            return
        if stage == "stopping" and state.get("manual_rollback"):
            # Nothing was restored yet. Restart the current deployment and keep
            # the completed update available for another rollback attempt.
            self.deployment.start()
            self.deployment.start(worker=True)
            self._unflag(state)
            self.save(state, "cancelled")
            return
        if stage in {"committed", "complete"}:
            # After commit, readers or mail may already have acted. Never replay
            # an older database snapshot automatically past this boundary.
            self.deployment.start()
            self.deployment.start(worker=True)
            self._unflag(state)
            self.save(state, "complete")
            return
        self.deployment.stop()
        if stage in REVERSIBLE:
            self.save(state, "rolling_back")
            self._restore(state, database)
            self.deployment.activate(state, previous=True)
        self._unflag(state)
        self.deployment.start()
        self.deployment.start(worker=True)
        self.save(state, "rolled_back")

    def rollback(self):
        """Explicitly restore the last completed update, saving current data first."""
        if self.require_privileges and (sys.platform != "linux" or os.geteuid() != 0):
            raise UpdateError("Run server recovery with sudo on the Linux host.")
        with self.lock():
            return self._rollback_locked()

    def _rollback_locked(self):
        database = DatabaseBackup(self.config, self.run)
        try:
            original = self.state()
            if original and original.get("manual_rollback") and original["stage"] == "cancelled":
                original = read_json(Path(original["rollback_of"]) / "journal.json")
            if not original or original["stage"] != "complete":
                raise UpdateError("There is no completed update to roll back. Use --resume for an interrupted transaction.")
            snapshot = Path(original["transaction"]) / "snapshot"
            if not (snapshot / "manifest.json").exists():
                raise UpdateError("The matching recovery snapshot is unavailable.")
            transaction = self.profile.at("backup_root") / ("rollback-" + uuid.uuid4().hex)
            transaction.mkdir(mode=0o700)
            state = {**original, "transaction": str(transaction), "snapshot_source": str(snapshot),
                     "rollback_of": original["transaction"], "manual_rollback": True}
            self.save(state, "prepared")
            try:
                self._flag(state)
                self.save(state, "stopping")
                self.deployment.stop()
                # Preserve all content received since the original update before
                # honoring the operator's explicit request to restore its snapshot.
                self._backup(state, database)
            except BaseException:
                self._recover(state, database)
                raise
            self.save(state, "rolling_back")
            self._recover(state, database)
            return state
        finally:
            database.close()

    def execute(self, update=None, *, resume=False):
        if self.require_privileges and (sys.platform != "linux" or os.geteuid() != 0):
            raise UpdateError("Run managed server updates with sudo on the Linux host.")
        self.profile.at("backup_root").mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.profile.at("backup_root"), 0o700)
        with self.lock():
            return self._execute_locked(update, resume=resume)

    def _execute_locked(self, update, *, resume):
        database = DatabaseBackup(self.config, self.run)
        try:
            state = self.state()
            if state and state["stage"] not in TERMINAL:
                if not resume:
                    raise UpdateError("An interrupted update needs recovery. Run quillsail update --resume before starting another update.")
                self._recover(state, database)
                return state
            if resume:
                return state
            self.deployment.preflight()
            database.preflight()
            if update is None or not update.available or update.target != "server":
                raise UpdateError("Check for an available signed server release before installing.")
            self.check_space(update, database)
            transaction = self.profile.at("backup_root") / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
            transaction.mkdir(mode=0o700)
            state = {"format": "quillsail.server-transaction.v1", "transaction": str(transaction),
                     "old_version": self.deployment.version(), "version": update.version, "profile": str(self.profile.path)}
            if update.current != state["old_version"]:
                raise UpdateError("The installed version changed. Check for updates again.")
            self.save(state, "preparing")
            if isinstance(self.run, CommandRunner):
                self.run.log_path = transaction / "commands.log"
            try:
                wheel = self.client.download(update, transaction / update.archive)
                state.update(self.deployment.prepare(wheel, update.version, transaction))
            except BaseException:
                self.save(state, "cancelled")
                raise
            self.save(state, "prepared")
            try:
                self._flag(state)
                self.save(state, "stopping")
                self.deployment.stop()
                self._backup(state, database)
                self.save(state, "backup_complete")
                identity = self._worker_identity(database)
                self.save(state, "activating")
                self.deployment.activate(state)
                self.save(state, "migrating")
                self.deployment.migrate()
                self.save(state, "validating")
                self.deployment.start()
                self.wait_health(update.version)
                self.deployment.start(worker=True)
                self._validate_worker(database, identity)
                self.save(state, "committed")
                self._unflag(state)
                self.save(state, "complete")
            except BaseException:
                # A killed process leaves its journal and gate for --resume.
                # Ordinary failures are recovered immediately while validation
                # is still reversible and the email worker remains paused.
                self._recover(state, database)
                raise
            return state
        finally:
            database.close()
