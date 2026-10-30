"""Shared fixtures: a release fixture, the three host apps, and signed TUF feeds with throwaway keys."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.exceptions import DownloadHTTPError
from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, TargetFile, Targets, Timestamp
from tuf.ngclient.fetcher import FetcherInterface

from quillsail_site import create_app
from quillsail_site.config import Config
from quillsail_site.docs import Docs
from quillsail_site.platforms import PLATFORMS, archive_name

REPO_ROOT = Path(__file__).resolve().parents[2]
FEED = "https://feed.example.test/metadata/"
MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
WINDOWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
LINUX_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def release_fixture(version="2.3.4", url_base="https://downloads.example.test", archives=None) -> dict:
    """A releases file in the format QUILLSAIL_RELEASES_FIXTURE reads."""
    builds = {}
    for target in PLATFORMS:
        data = (archives or {}).get(target, f"synthetic {target}".encode())
        name = archive_name(version, target)
        builds[target] = {"version": version, "archive": name, "size": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "url": f"{url_base}/{name}"}
    wheel = f"copydesk-{version}-py3-none-any.whl"
    server = {"version": version, "archive": wheel, "size": 658068, "sha256": "ab" * 32, "url": f"{url_base}/{wheel}"}
    return {"channels": {"beta": {"builds": builds, "server": server}}}


@pytest.fixture(scope="session")
def docs() -> Docs:
    # Rendering every page takes a moment, so tests share one copy.
    return Docs(REPO_ROOT, repository="Dooba-Digital-LLC/QuillSail")


@pytest.fixture
def fixture_file(tmp_path) -> Path:
    path = tmp_path / "releases.json"
    path.write_text(json.dumps(release_fixture()), encoding="utf-8")
    return path


@pytest.fixture
def config(fixture_file) -> Config:
    return Config.from_env({
        "QUILLSAIL_SITE_URL": "https://quillsail.test",
        "QUILLSAIL_DOCS_URL": "https://docs.quillsail.test",
        "QUILLSAIL_UPDATES_URL": "https://updates.quillsail.test",
    }, releases_fixture=fixture_file, warm_releases=False)


@pytest.fixture
def website(config, docs):
    return create_app(config, docs=docs)


@pytest.fixture
def site(website):
    return website.apps["site"].test_client()


@pytest.fixture
def docs_client(website):
    return website.apps["docs"].test_client()


@pytest.fixture
def updates(website):
    return website.apps["updates"].test_client()


# --- signed feeds ----------------------------------------------------------------------------------


class MemoryFetcher(FetcherInterface):
    def __init__(self, files: dict[str, bytes], base: str = FEED):
        self.files, self.base, self.urls = files, base, []
        self.fail = False

    def _fetch(self, url):
        self.urls.append(url)
        name = url[len(self.base):] if url.startswith(self.base) else None
        if self.fail or name not in self.files:
            raise DownloadHTTPError("Synthetic missing file", 404)
        yield self.files[name]


class Feed:
    """A small TUF repository signed with throwaway keys, like scripts/update_feed.py makes."""

    def __init__(self, tmp_path: Path):
        self.keys = {role: CryptoSigner.generate_ed25519() for role in ("root", "targets", "snapshot", "timestamp")}
        root = Metadata(Root(expires=datetime.now(UTC) + timedelta(days=365)))
        for role, signer in self.keys.items():
            root.signed.add_key(signer.public_key, role)
        root.sign(self.keys["root"])
        self.root = root.to_bytes()
        self.root_path = tmp_path / "root.json"
        self.root_path.write_bytes(self.root)
        self.files: dict[str, bytes] = {"1.root.json": self.root}
        self.version = 0

    def publish(self, entries: list[dict], *, expires_in=timedelta(days=7), targets_signer=None) -> None:
        """Sign targets for entries of {channel, target, version, data, [custom overrides]}."""
        self.version += 1
        now = datetime.now(UTC)
        targets = {}
        for entry in entries:
            target, version, channel = entry["target"], entry["version"], entry.get("channel", "beta")
            archive = f"copydesk-{version}-py3-none-any.whl" if target == "server" else archive_name(version, target)
            kind = "installers/" if entry.get("installer") else ""
            info = TargetFile.from_data(f"{channel}/{kind}{target}", entry.get("data", b"archive bytes"), ["sha256"])
            custom = {"version": version, "archive": archive, "target": target, "channel": channel,
                      "commit": "a" * 40, "updater_protocol": 1}
            if target == "server":
                custom["server_update_protocol"] = 1
            custom.update(entry.get("custom", {}))
            info.unrecognized_fields["custom"] = custom
            targets[info.path] = info
        signed_targets = Metadata(Targets(version=self.version, expires=now + timedelta(days=90), targets=targets))
        signed_targets.sign(targets_signer or self.keys["targets"])
        targets_bytes = signed_targets.to_bytes()
        snapshot = Metadata(Snapshot(version=self.version, expires=now + timedelta(days=14),
                                     meta={"targets.json": MetaFile.from_data(self.version, targets_bytes, ["sha256"])}))
        snapshot.sign(self.keys["snapshot"])
        snapshot_bytes = snapshot.to_bytes()
        timestamp = Metadata(Timestamp(version=self.version, expires=now + expires_in,
                                       snapshot_meta=MetaFile.from_data(self.version, snapshot_bytes, ["sha256"])))
        timestamp.sign(self.keys["timestamp"])
        self.files.update({f"{self.version}.targets.json": targets_bytes,
                           f"{self.version}.snapshot.json": snapshot_bytes,
                           "timestamp.json": timestamp.to_bytes()})

    def config(self, **overrides) -> Config:
        return Config.from_env({}, trust_root=self.root_path, metadata_url=FEED, warm_releases=False, **overrides)


@pytest.fixture
def feed(tmp_path) -> Feed:
    return Feed(tmp_path)


def every_platform(version="2.0.0", channel="beta") -> list[dict]:
    return [{"target": target, "version": version, "channel": channel, "data": f"{target} {version}".encode()}
            for target in PLATFORMS]
