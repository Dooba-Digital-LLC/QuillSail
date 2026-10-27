"""Authenticated desktop update discovery and preparation.

TUF's maintained client verifies metadata against a root shipped in the app.
Only a verified archive is handed to the separate replacement helper. There is
no unsigned fallback, shell command, elevation or publication-data replacement.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version
from tuf.api.exceptions import DownloadError, RepositoryError
from tuf.api.metadata import TargetFile
from tuf.ngclient import Updater, UpdaterConfig
from tuf.ngclient.urllib3_fetcher import Urllib3Fetcher

from copydesk import __version__
from copydesk.desktop import AlreadyRunning, InstanceLock, default_data_dir, write_private
from copydesk.update_install import PROTOCOL, InstallError, clean_environment, read_install

REPOSITORY = "Dooba-Digital-LLC/QuillSail"
METADATA_URL = f"https://github.com/{REPOSITORY}/releases/download/desktop-updates/"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
BOOTSTRAP = Path(__file__).parent / "updates/root.json"
CHANNELS = ("stable", "beta")
MAX_ARCHIVE = 2 * 1024**3


class UpdateError(RuntimeError):
    pass


def native_target():
    machine = platform.machine().lower()
    architecture = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x86_64", "x86_64": "x86_64"}.get(machine)
    system = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform)
    target = f"{system}-{architecture}"
    if target not in {"macos-arm64", "macos-x86_64", "windows-x86_64", "linux-x86_64"}:
        raise UpdateError("There is no desktop download for this operating system and processor.")
    return target


def cache_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/QuillSail/updates"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "QuillSailUpdateCache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "quillsail/updates"


def install_lock_path(installation: Path):
    identity = hashlib.sha256(os.fsencode(installation.resolve())).hexdigest()
    return cache_dir() / "installations" / identity / "running.lock"


def saved_channel():
    try:
        channel = json.loads((cache_dir() / "preferences.json").read_text(encoding="utf-8")).get("channel")
        if channel in CHANNELS:
            return channel
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return "beta"


def save_channel(channel):
    if channel not in CHANNELS:
        raise UpdateError("Choose the stable or beta update channel.")
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(folder / "preferences.json", json.dumps({"channel": channel}))


@dataclass(frozen=True)
class Update:
    current: str
    version: str
    target: str
    channel: str
    archive: str
    length: int
    sha256: str

    @property
    def available(self):
        return Version(self.version) > Version(self.current)

    @property
    def release_url(self):
        return f"{RELEASES_URL}/tag/v{self.version}"


class ProgressFetcher(Urllib3Fetcher):
    def __init__(self, progress=None):
        super().__init__(socket_timeout=15)
        self.progress, self.download_size = progress, None

    def _fetch(self, url):
        downloaded = 0
        for chunk in super()._fetch(url):
            downloaded += len(chunk)
            if self.progress and self.download_size:
                self.progress("Downloading update…", min(100, int(downloaded * 100 / self.download_size)))
            yield chunk


class UpdateClient:
    def __init__(self, *, cache=None, bootstrap=None, metadata_url=METADATA_URL, fetcher=None):
        self.cache = Path(cache or cache_dir())
        self.bootstrap = bootstrap if bootstrap is not None else BOOTSTRAP.read_bytes()
        self.metadata_url, self.fetcher = metadata_url, fetcher

    def _client(self, progress=None):
        metadata = self.cache / "metadata"
        metadata.mkdir(parents=True, exist_ok=True, mode=0o700)
        fetcher = self.fetcher or ProgressFetcher(progress)
        client = Updater(str(metadata), self.metadata_url, config=UpdaterConfig(
            prefix_targets_with_hash=False, max_root_rotations=32, max_delegations=0,
            app_user_agent=f"QuillSail/{__version__}"), bootstrap=self.bootstrap, fetcher=fetcher)
        return client, fetcher

    def _lock(self):
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = InstanceLock(self.cache / "update.lock")
        try:
            lock.acquire()
        except AlreadyRunning as exc:
            raise UpdateError("Another update check is running. Try again when it finishes.") from exc
        return lock

    @staticmethod
    def _info(client, channel, target, current):
        if channel not in CHANNELS:
            raise UpdateError("Choose the stable or beta update channel.")
        info = client.get_targetinfo(f"{channel}/{target}")
        if info is None:
            raise UpdateError(f"No {channel} desktop release is available for this computer.")
        custom = info.unrecognized_fields.get("custom", {})
        if not isinstance(custom, dict):
            raise UpdateError("The signed update has invalid application metadata.")
        version = custom.get("version", "")
        try:
            parsed = Version(version)
            Version(current)
            if (str(parsed) != version or len(parsed.release) != 3 or parsed.is_prerelease or parsed.is_devrelease
                    or parsed.local or parsed.epoch):
                raise ValueError
        except (InvalidVersion, ValueError, TypeError) as exc:
            raise UpdateError("The signed update has an invalid version.") from exc
        suffix = ".tar.gz" if target.startswith("linux-") else ".zip"
        archive = f"QuillSail-{version}-{target}{suffix}"
        digest = info.hashes.get("sha256", "")
        if (custom.get("archive") != archive or custom.get("target") != target or custom.get("channel") != channel
                or custom.get("updater_protocol") != PROTOCOL or type(info.length) is not int or not 0 < info.length <= MAX_ARCHIVE
                or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
            raise UpdateError("The signed update does not match this application or computer.")
        return Update(current, version, target, channel, archive, info.length, digest)

    def check(self, *, channel="beta", target=None, current=__version__):
        lock = self._lock()
        try:
            client, _fetcher = self._client()
            client.refresh()
            return self._info(client, channel, target or native_target(), current)
        except (DownloadError, RepositoryError, OSError) as exc:
            raise UpdateError("The update service could not be verified or reached. Your installed version is unchanged. Try again later.") from exc
        finally:
            lock.release()

    def prepare(self, update: Update, installation: Path, *, data_dir=None, restart=True, progress=None):
        installation = installation.absolute()
        if installation.is_symlink() or "AppTranslocation" in installation.parts:
            raise UpdateError("Move QuillSail to a writable application folder before updating it.")
        installed = read_install(installation)
        if installed["target"] != update.target or installed["version"] != update.current:
            raise UpdateError("The installation changed. Check for updates again.")
        publication = Path(data_dir or default_data_dir()).expanduser().resolve()
        if publication.is_relative_to(installation.resolve()) or installation.resolve().is_relative_to(publication):
            raise UpdateError("Keep publication data outside the application folder before updating.")
        lock, transaction = self._lock(), None
        try:
            client, fetcher = self._client(progress)
            client.refresh()
            latest = self._info(client, update.channel, update.target, update.current)
            if latest != update or not latest.available:
                raise UpdateError("The available update changed. Check for updates again.")
            if shutil.disk_usage(installation.parent).free < update.length * 5 + 256 * 1024**2:
                raise UpdateError("There is not enough free disk space to stage this update safely.")
            transaction = Path(tempfile.mkdtemp(prefix=".quillsail-update-", dir=installation.parent))
            if hasattr(fetcher, "download_size"):
                fetcher.download_size = update.length
            # The filename and hash both come from the signed TUF target.
            target_info = TargetFile(update.length, {"sha256": update.sha256}, update.archive)
            client.download_target(target_info, str(transaction / "update.archive"),
                                   target_base_url=f"{RELEASES_URL}/download/v{update.version}/")
            if progress:
                progress("Preparing installer…", -1)
            helper_source = installation / "Contents/Resources/updater" if update.target.startswith("macos-") else installation / "updater"
            if not helper_source.is_dir():
                raise UpdateError("This installation has no update helper. Install a newer desktop build manually once.")
            shutil.copytree(helper_source, transaction / "helper", symlinks=True)
            plan = {"protocol": PROTOCOL, "transaction": str(transaction), "installation": str(installation.resolve()),
                    "target": update.target, "previous_version": update.current, "version": update.version,
                    "length": update.length, "sha256": update.sha256, "parent_pid": os.getpid(),
                    "restart": restart, "data_dir": str(publication), "install_lock": str(install_lock_path(installation))}
            write_private(transaction / "plan.json", json.dumps(plan))
            return transaction / "plan.json"
        except (DownloadError, RepositoryError, OSError, InstallError) as exc:
            raise UpdateError("The update could not be downloaded or prepared. Your installed version is unchanged.") from exc
        finally:
            lock.release()
            if transaction is not None and not (transaction / "plan.json").is_file():
                shutil.rmtree(transaction, ignore_errors=True)


def installation_path(path=None):
    if path is not None:
        root = Path(path).expanduser().absolute()
    elif getattr(sys, "frozen", False):
        exe = Path(sys.executable).absolute()
        root = exe.parents[2] if sys.platform == "darwin" else exe.parent
    else:
        raise UpdateError("For a desktop installation, use --app with the application folder. Source and server installs remain managed by Git, pip or Docker.")
    try:
        read_install(root)
    except InstallError as exc:
        raise UpdateError(str(exc)) from exc
    return root


def launch_installer(plan_path: Path):
    transaction = plan_path.parent
    helper = transaction / "helper" / ("QuillSailUpdater.exe" if sys.platform == "win32" else "QuillSailUpdater")
    process = subprocess.Popen([str(helper), "--plan", str(plan_path)], cwd=transaction,
                               env=clean_environment(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (transaction / "helper-ready").is_file():
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        process.terminate()
    raise UpdateError("The update installer could not start. Your installed version is unchanged.")


def last_install_result(installation: Path):
    """Surface detached installer failures when the existing app is reopened."""
    try:
        transactions = sorted(installation.parent.glob(".quillsail-update-*"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for transaction in transactions[:50]:
        try:
            if (transaction / "result.json").stat().st_size > 16_384:
                continue
            plan = json.loads((transaction / "plan.json").read_text(encoding="utf-8"))
            if Path(plan["installation"]).resolve() == installation.resolve():
                return json.loads((transaction / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None
