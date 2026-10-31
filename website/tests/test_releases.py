"""Release information comes only from a verified, signed update feed."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import REPO_ROOT, MemoryFetcher, every_platform
from quillsail_site.config import Config
from quillsail_site.platforms import detect
from quillsail_site.releases import Build, Catalog, ReleaseError, load_fixture


def catalog_for(feed, **kwargs):
    fetcher = MemoryFetcher(feed.files)
    return Catalog(feed.config(), fetcher_factory=lambda: fetcher, **kwargs), fetcher


def test_signed_feed_lists_verified_downloads(feed):
    entries = every_platform("2.0.0") + [{"target": "server", "version": "2.0.0", "data": b"wheel"}]
    feed.publish(entries)
    catalog, _fetcher = catalog_for(feed)

    release = catalog.release("beta")

    assert release.version == "2.0.0" and release.source == "signed feed"
    mac = release.builds["macos-arm64"]
    assert mac.archive == "QuillSail-2.0.0-macos-arm64.zip"
    assert mac.sha256 == hashlib.sha256(b"macos-arm64 2.0.0").hexdigest()
    assert mac.size == len(b"macos-arm64 2.0.0")
    assert mac.url == ("https://github.com/DoobaDigital/QuillSail/releases/download/v2.0.0/"
                       "QuillSail-2.0.0-macos-arm64.zip")
    assert release.builds["linux-x86_64"].archive.endswith(".tar.gz")
    assert release.server.archive == "copydesk-2.0.0-py3-none-any.whl"
    assert release.notes_url.endswith("/releases/tag/v2.0.0")
    assert catalog.release("stable") is None


def test_each_channel_is_separate(feed):
    feed.publish(every_platform("2.0.0", "beta") + every_platform("1.9.0", "stable"))
    catalog, _fetcher = catalog_for(feed)
    assert catalog.release("beta").version == "2.0.0"
    assert catalog.release("stable").version == "1.9.0"


@pytest.mark.parametrize("custom", [
    {"archive": "QuillSail-2.0.0-macos-arm64.dmg"},
    {"target": "windows-x86_64"},
    {"channel": "stable"},
    {"updater_protocol": 2},
    {"version": "2.0.0rc1"},
    {"version": "2.0"},
])
def test_entries_that_dont_match_their_download_are_rejected(feed, custom):
    entries = every_platform("2.0.0")
    entries[0]["custom"] = custom
    feed.publish(entries)
    catalog, _fetcher = catalog_for(feed)

    assert catalog.releases() == {}
    assert "ReleaseError" in catalog.status()["last_error"]


def test_metadata_signed_by_an_untrusted_key_is_rejected(feed):
    from securesystemslib.signer import CryptoSigner

    feed.publish(every_platform(), targets_signer=CryptoSigner.generate_ed25519())
    catalog, _fetcher = catalog_for(feed)

    assert catalog.release() is None
    assert catalog.status()["available"] is False


def test_expired_metadata_is_rejected(feed):
    feed.publish(every_platform(), expires_in=timedelta(seconds=-5))
    catalog, _fetcher = catalog_for(feed)
    assert catalog.release() is None
    assert "Expired" in catalog.status()["last_error"]


def test_the_last_verified_release_is_kept_when_the_feed_fails(feed):
    feed.publish(every_platform("2.0.0"))
    clock = [0.0]
    catalog, fetcher = catalog_for(feed, clock=lambda: clock[0])
    assert catalog.release().version == "2.0.0"

    fetcher.fail = True
    catalog.refresh()

    assert catalog.release().version == "2.0.0"
    assert catalog.status()["last_error"]


def test_a_newer_signed_release_replaces_the_old_one(feed):
    feed.publish(every_platform("2.0.0"))
    catalog, _fetcher = catalog_for(feed)
    assert catalog.release().version == "2.0.0"

    feed.publish(every_platform("2.1.0"))
    catalog.refresh()

    assert catalog.release().version == "2.1.0"
    assert catalog.status()["last_error"] is None


def test_failed_first_loads_are_retried_after_a_pause(feed):
    clock = [100.0]
    catalog, fetcher = catalog_for(feed, clock=lambda: clock[0])  # Nothing published yet.
    assert catalog.release() is None
    requests = len(fetcher.urls)

    catalog.release()
    assert len(fetcher.urls) == requests  # Within the pause, nothing is fetched again.

    feed.publish(every_platform("2.0.0"))
    clock[0] += 31
    assert catalog.release().version == "2.0.0"


def test_stale_releases_refresh_in_the_background(feed):
    feed.publish(every_platform("2.0.0"))
    clock = [0.0]
    catalog, _fetcher = catalog_for(feed, clock=lambda: clock[0])
    catalog.release()
    feed.publish(every_platform("2.0.1"))
    clock[0] += catalog.config.release_ttl + 1

    assert catalog.release().version == "2.0.0"  # Served at once, while the refresh runs.
    for _ in range(200):
        if catalog.release().version == "2.0.1":
            break
        import time
        time.sleep(0.01)
    assert catalog.release().version == "2.0.1"


def test_fixture_files_are_validated(tmp_path):
    path = tmp_path / "releases.json"
    path.write_text(json.dumps({"channels": {"beta": {"builds": {"macos-arm64": {
        "version": "1.0.0", "archive": "x.zip", "size": 1, "sha256": "not-a-digest", "url": "https://x"}}}}}))
    with pytest.raises(ReleaseError):
        load_fixture(Config.from_env({}), path)


def test_the_trust_root_matches_the_app():
    app_root = REPO_ROOT / "src/copydesk/updates/root.json"
    site_root = Path(__file__).resolve().parents[1] / "quillsail_site/trust/root.json"
    assert site_root.read_bytes() == app_root.read_bytes(), (
        "Copy src/copydesk/updates/root.json to website/quillsail_site/trust/root.json")


@pytest.mark.parametrize(("agent", "expected"), [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "macos-arm64"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "windows-x86_64"),
    ("Mozilla/5.0 (X11; Linux x86_64)", "linux-x86_64"),
    ("Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0", "linux-x86_64"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148", None),
    ("Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile Safari/537.36", None),
    ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0)", None),
    ("", None),
])
def test_download_detection(agent, expected):
    assert detect(agent) == expected


def test_size_labels():
    assert Build("server", "1.0.0", "a", 658068, "0" * 64, "https://x").size_label == "658 KB"
    assert Build("macos-arm64", "1.0.0", "a", 65264665, "0" * 64, "https://x").size_label == "65 MB"


def test_graphical_downloads_are_separately_verified_and_preserve_archive_api(feed, docs):
    from quillsail_site.platforms import INSTALLER_SUFFIXES
    from quillsail_site.views.site import create_site_app

    entries = every_platform("2.0.0")
    for target, suffix in INSTALLER_SUFFIXES.items():
        entries.append({"target": target, "version": "2.0.0", "installer": True,
                        "custom": {"archive": f"QuillSail-2.0.0-{target}{suffix}", "purpose": "installer"}})
    feed.publish(entries)
    catalog, _ = catalog_for(feed)
    release = catalog.release()
    assert len(release.installers) == 4
    site = create_site_app(feed.config(), catalog, docs).test_client()
    assert site.get("/download/windows-x86_64").location.endswith("-setup.exe")
    assert site.get("/api/v1/releases/latest/windows-x86_64").json["archive"].endswith(".zip")
    assert site.get("/api/v1/releases/latest/windows-x86_64/installer").json["archive"].endswith("-setup.exe")
    assert len(site.get("/api/v1/releases/latest").json["installers"]) == 4
    page = site.get("/download").text
    assert "setup wizard" in page and ".deb" in page and "Applications" in page
    assert "Extract All" not in page


@pytest.mark.parametrize("custom", [
    {"archive": "wrong.dmg", "purpose": "installer"},
    {"archive": "QuillSail-2.0.0-macos-arm64.dmg", "purpose": "update"},
    {"archive": "QuillSail-3.0.0-macos-arm64.dmg", "purpose": "installer", "version": "3.0.0"},
])
def test_invalid_or_mismatched_graphical_installer_is_rejected(feed, custom):
    feed.publish(every_platform("2.0.0") + [{"target": "macos-arm64", "version": "2.0.0",
                                           "installer": True, "custom": custom}])
    catalog, _ = catalog_for(feed)
    assert catalog.releases() == {}
