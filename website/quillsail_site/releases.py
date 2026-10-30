"""Which QuillSail downloads exist, from the same signed feed the app updates from.

The desktop app's updater (copydesk/updater.py) trusts a TUF root shipped in the
package. The website uses a copy of that root to verify the feed, so the
download buttons, the install scripts and the release API only offer archives
whose exact size and SHA-256 digest are signed. The installers check those
values before installing anything.

Verified results are kept in memory. They are refreshed in the background
after `release_ttl` seconds; if GitHub is unreachable, the last verified result
stays in use.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import certifi
import urllib3
from packaging.version import InvalidVersion, Version
from tuf.api.exceptions import DownloadError, DownloadHTTPError, RepositoryError
from tuf.api.metadata import TargetFile
from tuf.ngclient import Updater, UpdaterConfig
from tuf.ngclient.fetcher import FetcherInterface

from quillsail_site.config import CHANNELS, Config
from quillsail_site.platforms import INSTALLER_SUFFIXES, PLATFORMS, archive_name

log = logging.getLogger(__name__)

# Matches copydesk.update_install.PROTOCOL; a newer protocol needs a newer app.
UPDATER_PROTOCOL = 1
MAX_ARCHIVE = 2 * 1024**3
RETRY_AFTER_FAILURE = 30
SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Build:
    target: str  # a key of PLATFORMS, or "server"
    version: str
    archive: str
    size: int
    sha256: str
    url: str

    @property
    def size_label(self) -> str:
        if self.size < 1_000_000:
            return f"{max(1, round(self.size / 1000))} KB"
        return f"{round(self.size / 1_000_000)} MB"


@dataclass(frozen=True)
class Release:
    channel: str
    version: str
    builds: dict[str, Build]
    server: Build | None
    notes_url: str
    checked_at: datetime
    source: str  # "signed feed" or "fixture"
    installers: dict[str, Build] = field(default_factory=dict)

    @property
    def downloads(self) -> dict[str, Build]:
        return {**self.builds, **self.installers}


class ReleaseError(RuntimeError):
    pass


class HTTPSFetcher(FetcherInterface):
    """Download metadata with certificate verification, following GitHub's redirects."""

    def __init__(self, timeout: float = 15):
        self._pool = urllib3.PoolManager(
            cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
            timeout=urllib3.Timeout(connect=timeout, read=timeout),
            retries=urllib3.Retry(total=3, redirect=5, backoff_factor=0.5, status_forcelist=(502, 503, 504)),
            headers={"User-Agent": "QuillSail-website (+https://github.com/Dooba-Digital-LLC/QuillSail)"},
        )

    def _fetch(self, url: str) -> Iterator[bytes]:
        try:
            response = self._pool.request("GET", url, preload_content=False)
        except urllib3.exceptions.HTTPError as exc:
            raise DownloadError(f"Could not download {url}") from exc
        try:
            if response.status != 200:
                raise DownloadHTTPError(f"HTTP {response.status} for {url}", response.status)
            yield from response.stream(64 * 1024)
        except urllib3.exceptions.HTTPError as exc:
            raise DownloadError(f"Download of {url} was interrupted") from exc
        finally:
            response.release_conn()


def _strict_version(value: object) -> str:
    """Accept only plain X.Y.Z release versions, as the app's updater does."""
    try:
        parsed = Version(str(value))
    except InvalidVersion:
        raise ReleaseError("The signed feed lists an invalid version.") from None
    if (str(parsed) != value or len(parsed.release) != 3 or parsed.is_prerelease or parsed.is_devrelease
            or parsed.local or parsed.epoch):
        raise ReleaseError("The signed feed lists an invalid version.")
    return str(value)


def validate_target(info: TargetFile, channel: str, target: str, base_url: str, *, installer: bool = False) -> Build:
    """Check one signed target the way copydesk.updater.UpdateClient._info does."""
    custom = info.unrecognized_fields.get("custom", {})
    if not isinstance(custom, dict):
        raise ReleaseError(f"The signed {channel}/{target} entry has no release details.")
    version = _strict_version(custom.get("version", ""))
    archive = f"copydesk-{version}-py3-none-any.whl" if target == "server" else archive_name(version, target)
    if installer:
        archive = f"QuillSail-{version}-{target}{INSTALLER_SUFFIXES[target]}"
        if custom.get("purpose") != "installer":
            raise ReleaseError("The signed installer entry has the wrong purpose.")
    digest = info.hashes.get("sha256", "")
    if (custom.get("archive") != archive or custom.get("target") != target or custom.get("channel") != channel
            or custom.get("updater_protocol") != UPDATER_PROTOCOL
            or (target == "server" and custom.get("server_update_protocol") != 1)
            or type(info.length) is not int or not 0 < info.length <= MAX_ARCHIVE
            or not SHA256.fullmatch(digest)):
        raise ReleaseError(f"The signed {channel}/{target} entry doesn't match the expected download.")
    return Build(target, version, archive, info.length, digest, f"{base_url}/download/v{version}/{archive}")


def _release(channel: str, builds: dict[str, Build], server: Build | None, config: Config, source: str) -> Release | None:
    if not builds:
        return None
    version = str(max(Version(build.version) for build in builds.values()))
    return Release(channel, version, builds, server, f"{config.releases_url}/tag/v{version}",
                   datetime.now(UTC).replace(microsecond=0), source)


def load_signed(config: Config, fetcher: FetcherInterface, metadata_dir: Path) -> dict[str, Release]:
    """Verify the TUF feed and return the newest signed release for each channel."""
    updater = Updater(
        str(metadata_dir), config.feed_url, fetcher=fetcher, bootstrap=config.trust_root.read_bytes(),
        config=UpdaterConfig(prefix_targets_with_hash=False, max_root_rotations=32, max_delegations=0,
                             app_user_agent="QuillSail-website"),
    )
    updater.refresh()
    releases = {}
    for channel in CHANNELS:
        builds, installers = {}, {}
        for target in PLATFORMS:
            info = updater.get_targetinfo(f"{channel}/{target}")
            if info is not None:
                builds[target] = validate_target(info, channel, target, config.releases_url)
            info = updater.get_targetinfo(f"{channel}/installers/{target}")
            if info is not None:
                installer = validate_target(info, channel, target, config.releases_url, installer=True)
                if target not in builds or installer.version != builds[target].version:
                    raise ReleaseError("Installer and updater versions do not match.")
                installers[target] = installer
        info = updater.get_targetinfo(f"{channel}/server")
        server = validate_target(info, channel, "server", config.releases_url) if info is not None else None
        release = _release(channel, builds, server, config, "signed feed")
        if release:
            release.installers.update(installers)
            releases[channel] = release
    return releases


def load_fixture(config: Config, path: Path) -> dict[str, Release]:
    """Read releases from a JSON file. For development and installer tests only."""
    data = json.loads(path.read_text(encoding="utf-8"))
    releases = {}
    for channel, entry in data.get("channels", {}).items():
        if channel not in CHANNELS:
            raise ReleaseError(f"Unknown channel {channel!r} in {path}.")
        builds = {}
        for target, item in entry.get("builds", {}).items():
            if target not in PLATFORMS:
                raise ReleaseError(f"Unknown platform {target!r} in {path}.")
            builds[target] = _fixture_build(target, item)
        server = _fixture_build("server", entry["server"]) if entry.get("server") else None
        release = _release(channel, builds, server, config, "fixture")
        if release:
            releases[channel] = release
    return releases


def _fixture_build(target: str, item: dict) -> Build:
    version = _strict_version(item["version"])
    if not SHA256.fullmatch(item["sha256"]) or not str(item["url"]).startswith(("http://", "https://")):
        raise ReleaseError(f"The {target} fixture needs a SHA-256 digest and an http(s) URL.")
    return Build(target, version, item["archive"], int(item["size"]), item["sha256"], item["url"])


class Catalog:
    """The current releases, shared by every request in one process."""

    def __init__(self, config: Config, *, fetcher_factory: Callable[[], FetcherInterface] = HTTPSFetcher,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config
        self._fetcher_factory, self._clock = fetcher_factory, clock
        self._lock = threading.Lock()
        self._releases: dict[str, Release] | None = None
        self._loaded_at = self._failed_at = None
        self._error: str | None = None
        self._refreshing = False
        self._metadata_dir: Path | None = None

    # --- reading ----------------------------------------------------------------------

    def release(self, channel: str | None = None) -> Release | None:
        return self.releases().get(channel or self.config.channel)

    def releases(self) -> dict[str, Release]:
        if self._releases is None:
            self._load_once()
        elif self._clock() - self._loaded_at > self.config.release_ttl:
            self.refresh_in_background()
        return self._releases or {}

    def status(self) -> dict:
        releases = self._releases or {}
        return {
            "available": bool(releases),
            "source": next(iter(releases.values())).source if releases else None,
            "channels": {channel: release.version for channel, release in releases.items()},
            "checked_at": max((r.checked_at for r in releases.values()), default=None),
            "last_error": self._error,
        }

    # --- loading ----------------------------------------------------------------------

    def _load_once(self) -> None:
        with self._lock:
            if self._releases is not None:
                return
            if self._failed_at is not None and self._clock() - self._failed_at < RETRY_AFTER_FAILURE:
                return
            self._refresh_locked()

    def refresh(self) -> None:
        with self._lock:
            self._refresh_locked()

    def refresh_in_background(self) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._background, name="release-refresh", daemon=True).start()

    def _background(self) -> None:
        try:
            self.refresh()
        finally:
            self._refreshing = False

    def _refresh_locked(self) -> None:
        try:
            if self.config.releases_fixture:
                releases = load_fixture(self.config, self.config.releases_fixture)
            else:
                if self._metadata_dir is None:
                    self._metadata_dir = Path(tempfile.mkdtemp(prefix="quillsail-site-tuf-"))
                releases = load_signed(self.config, self._fetcher_factory(), self._metadata_dir)
        except (DownloadError, RepositoryError, ReleaseError, OSError, ValueError, KeyError) as exc:
            self._failed_at, self._error = self._clock(), f"{type(exc).__name__}: {exc}"
            if self._releases is not None:
                # Keep serving what was last verified, and try again later.
                self._loaded_at = self._clock()
            log.warning("Could not verify QuillSail releases: %s", self._error)
            return
        self._releases, self._loaded_at, self._error, self._failed_at = releases, self._clock(), None, None
        log.info("Verified QuillSail releases: %s",
                 ", ".join(f"{channel} {release.version}" for channel, release in releases.items()) or "none")
