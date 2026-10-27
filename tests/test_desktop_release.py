import copy
import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = script("publish_desktop")
builder = script("build_desktop")
VERSION, COMMIT = "1.7.0", "a" * 40


def downloads(folder):
    for target, extension in release.TARGETS.items():
        stem = f"QuillSail-{VERSION}-{target}"
        archive = folder / (stem + extension)
        archive.write_bytes(b"synthetic-disposable-bundle:" + target.encode())
        report = {"version": VERSION, "target": target, "archive": archive.name, "commit": COMMIT,
                  "sha256": release.digest(archive), "setup_restart_check": True, "gui_lifecycle_check": True,
                  "updater_install_check": True}
        (folder / (stem + ".json")).write_text(json.dumps(report))


@pytest.mark.parametrize(("system", "machine", "expected"), [
    ("Darwin", "arm64", "macos-arm64"), ("Darwin", "x86_64", "macos-x86_64"),
    ("Windows", "AMD64", "windows-x86_64"), ("Linux", "x86_64", "linux-x86_64"),
])
def test_native_architecture_produces_unambiguous_download_names(system, machine, expected):
    assert builder.target_name(system, machine) == expected


def test_other_architectures_are_not_mislabeled_as_supported_downloads():
    with pytest.raises(ValueError, match="Unsupported"):
        builder.target_name("Windows", "arm64")


def test_release_requires_four_tested_archives_with_matching_checksums_and_commit(tmp_path):
    downloads(tmp_path)
    files = release.validate_downloads(tmp_path, VERSION, COMMIT)
    assert len(files) == 6
    manifest = json.loads((tmp_path / "desktop-builds.json").read_text())
    assert {report["target"] for report in manifest["builds"]} == set(release.TARGETS)
    assert all(release.digest(tmp_path / report["archive"]) == report["sha256"] for report in manifest["builds"])
    assert len((tmp_path / "SHA256SUMS.txt").read_text().splitlines()) == 4


def test_combined_release_requires_tested_matching_server_package(tmp_path):
    downloads(tmp_path)
    wheel, source = tmp_path / f"copydesk-{VERSION}-py3-none-any.whl", tmp_path / f"copydesk-{VERSION}.tar.gz"
    wheel.write_bytes(b"synthetic portable server package")
    source.write_bytes(b"synthetic source package")
    report = {"version": VERSION, "commit": COMMIT, "archive": wheel.name, "source": source.name,
              "sha256": release.digest(wheel), "source_sha256": release.digest(source),
              "package_install_check": True, "server_update_check": True, "server_update_protocol": 1}
    (tmp_path / "server-build.json").write_text(json.dumps(report))
    files = release.validate_downloads(tmp_path, VERSION, COMMIT, include_server=True)
    assert len(files) == 9 and wheel in files and source in files
    wheel.write_bytes(b"changed after verification")
    with pytest.raises(ValueError, match="changed"):
        release.validate_downloads(tmp_path, VERSION, COMMIT, include_server=True)


@pytest.mark.parametrize("problem", ["missing", "tampered", "untested", "old-commit", "extra"])
def test_incomplete_or_unverified_release_is_rejected(tmp_path, problem):
    downloads(tmp_path)
    archive = tmp_path / f"QuillSail-{VERSION}-windows-x86_64.zip"
    report_path = archive.with_suffix(".json")
    report = json.loads(report_path.read_text())
    if problem == "missing":
        archive.unlink()
    elif problem == "tampered":
        archive.write_bytes(b"changed after testing")
    elif problem == "untested":
        report["gui_lifecycle_check"] = False
    elif problem == "old-commit":
        report["commit"] = "b" * 40
    else:
        (tmp_path / "outdated.zip").write_bytes(b"outdated build")
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        release.validate_downloads(tmp_path, VERSION, COMMIT)
    assert not (tmp_path / "SHA256SUMS.txt").exists()


class FakeGitHub:
    repository = "example/project"

    def __init__(self, *, fail_upload=False):
        self.release = None
        self.tag = None
        self.uploads, self.fail_upload, self.deleted = 0, fail_upload, []

    def request(self, method, endpoint, payload=None, *, upload=False, data=None):
        if upload:
            self.uploads += 1
            if self.fail_upload and self.uploads == 3:
                raise RuntimeError("Synthetic upload interruption")
            name = parse_qs(urlsplit(endpoint).query)["name"][0]
            import hashlib

            self.release["assets"].append({"id": self.uploads, "name": name, "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
            return self.release["assets"][-1]
        if method == "GET":
            if endpoint.startswith("git/ref/"):
                return copy.deepcopy(self.tag)
            if endpoint.startswith("releases?"):
                return [copy.deepcopy(self.release)] if self.release else []
            return copy.deepcopy(self.release)
        if method == "POST":
            self.release = {**payload, "id": 7, "assets": [], "html_url": "https://example.test/release"}
        elif method == "PATCH":
            assert len(self.release["assets"]) == 6
            self.release.update(payload)
        elif method == "DELETE":
            self.deleted.append(endpoint)
        return copy.deepcopy(self.release)


def test_publishing_waits_for_all_uploads_and_resumes_the_same_draft(tmp_path):
    downloads(tmp_path)
    files = release.validate_downloads(tmp_path, VERSION, COMMIT)
    client = FakeGitHub(fail_upload=True)
    with pytest.raises(RuntimeError, match="interruption"):
        release.publish(client, VERSION, COMMIT, files)
    assert client.release["draft"] is True
    client.fail_upload = False
    result = release.publish(client, VERSION, COMMIT, files)
    assert result == "https://example.test/release"
    assert client.release["draft"] is False and client.release["prerelease"] is True
    assert not client.deleted
    assert len(client.release["assets"]) == 6
    uploads = client.uploads
    assert release.publish(client, VERSION, COMMIT, files) == result
    assert client.uploads == uploads


def test_a_published_version_cannot_be_overwritten(tmp_path):
    downloads(tmp_path)
    files = release.validate_downloads(tmp_path, VERSION, COMMIT)
    client = FakeGitHub()
    release.publish(client, VERSION, COMMIT, files)
    client.release["assets"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="already published"):
        release.publish(client, VERSION, COMMIT, files)
    assert not client.deleted


def test_a_different_commit_cannot_take_over_an_existing_draft(tmp_path):
    downloads(tmp_path)
    files = release.validate_downloads(tmp_path, VERSION, COMMIT)
    client = FakeGitHub()
    client.release = {"draft": True, "target_commitish": "b" * 40}
    with pytest.raises(ValueError, match="different commit"):
        release.publish(client, VERSION, COMMIT, files)
    assert not client.uploads


def test_an_existing_tag_cannot_publish_downloads_from_a_different_commit(tmp_path):
    downloads(tmp_path)
    files = release.validate_downloads(tmp_path, VERSION, COMMIT)
    client = FakeGitHub()
    client.tag = {"object": {"type": "commit", "sha": "b" * 40}}
    with pytest.raises(ValueError, match="version tag"):
        release.publish(client, VERSION, COMMIT, files)
    assert not client.uploads and client.release is None
