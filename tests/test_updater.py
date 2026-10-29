"""Signed disposable repositories and synthetic desktop bundles; no live network."""

import hashlib
import io
import json
import os
import stat
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from tuf.api.exceptions import DownloadHTTPError, LengthOrHashMismatchError
from tuf.api.metadata import Metadata
from tuf.ngclient.fetcher import FetcherInterface

from copydesk import update_install
from copydesk.cli import main
from copydesk.update_metadata import (
    build_metadata,
    initialize,
    online_keys,
    read_published_metadata,
    release_targets,
    server_targets,
)
from copydesk.updater import RELEASES_URL, UpdateClient, UpdateError, installation_path

BASE = "https://updates.example.test/metadata/"
TARGET = "linux-x86_64"


class MemoryFetcher(FetcherInterface):
    def __init__(self):
        self.content, self.urls = {}, []

    def _fetch(self, url):
        self.urls.append(url)
        if url not in self.content:
            raise DownloadHTTPError("Synthetic missing file", 404)
        yield self.content[url]


def bundle_bytes(version="2.0.0", target=TARGET, extra=None):
    output = io.BytesIO()
    root = "QuillSail.app" if target.startswith("macos-") else "QuillSail"
    executable = "Contents/MacOS/QuillSail" if target.startswith("macos-") else "QuillSail.exe" if target.startswith("windows-") else "QuillSail"
    marker = "Contents/Resources/" + update_install.MARKER if target.startswith("macos-") else update_install.MARKER
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo(root + "/" + executable)
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(info, "new executable")
        archive.writestr(root + "/" + marker, json.dumps({"format": "quillsail.install.v1", "version": version,
                                                        "target": target, "updater_protocol": 1}))
        if extra:
            for name, content in extra.items():
                archive.writestr(name, content)
    return output.getvalue()


def install_at(path, version="1.0.0", target=TARGET):
    path.mkdir()
    exe = update_install.executable(path, target)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("old executable")
    marker = update_install.marker_path(path, target)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"format": "quillsail.install.v1", "version": version, "target": target, "updater_protocol": 1}))
    resources = path / "Contents/Resources" if target.startswith("macos-") else path
    (resources / "updater").mkdir()
    (resources / "updater/QuillSailUpdater").write_text("synthetic helper")
    return path


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setattr("copydesk.updater.cache_dir", lambda: tmp_path / "user-cache")
    root_path, keys_path = tmp_path / "root.json", tmp_path / "keys.json"
    root = initialize(keys_path, root_path)
    keys = online_keys(keys_path.read_text())
    fetcher = MemoryFetcher()

    def publish(version="2.0.0", sequence=1, now=None, target=TARGET, mutate=None):
        archive = bundle_bytes(version, target)
        name = f"QuillSail-{version}-{target}.tar.gz" if target.startswith("linux-") else f"QuillSail-{version}-{target}.zip"
        (tmp_path / name).write_bytes(archive)
        report = {"version": version, "target": target, "archive": name, "commit": "a" * 40,
                  "sha256": hashlib.sha256(archive).hexdigest(), "setup_restart_check": True,
                  "gui_lifecycle_check": True, "updater_install_check": True}
        targets = release_targets(tmp_path, [report], "beta")
        if mutate:
            mutate(targets[f"beta/{target}"])
        metadata = build_metadata(root, keys, targets, sequence, now=now)
        fetcher.content.update({BASE + name: value for name, value in metadata.items()})
        fetcher.content[f"{RELEASES_URL}/download/v{version}/{name}"] = archive
        return metadata

    publish()
    client = UpdateClient(cache=tmp_path / "cache", bootstrap=root_path.read_bytes(), metadata_url=BASE, fetcher=fetcher)
    return client, fetcher, publish


def test_signed_update_is_selected_for_exact_channel_platform_and_newer_version(repository):
    client, _fetcher, _publish = repository
    update = client.check(target=TARGET, current="1.0.0")
    assert update.version == "2.0.0" and update.available
    assert update.channel == "beta" and update.target == TARGET
    assert not client.check(target=TARGET, current="2.0.0").available
    assert not client.check(target=TARGET, current="3.0.0").available
    with pytest.raises(UpdateError, match="No stable"):
        client.check(target=TARGET, current="1.0.0", channel="stable")
    with pytest.raises(UpdateError, match="No beta"):
        client.check(target="macos-arm64", current="1.0.0")


@pytest.mark.parametrize("tamper", [False, True])
def test_signed_server_wheel_uses_authenticated_metadata_and_download_hash(tmp_path, tamper):
    root_path, keys_path = tmp_path / "root.json", tmp_path / "keys.json"
    root = initialize(keys_path, root_path)
    fetcher = MemoryFetcher()
    wheel = tmp_path / "copydesk-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"synthetic server package")
    report = {"version": "2.0.0", "archive": wheel.name, "commit": "a" * 40,
              "server_update_check": True, "package_install_check": True, "server_update_protocol": 1,
              "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    targets = server_targets(tmp_path, report, "beta")
    metadata = build_metadata(root, online_keys(keys_path.read_text()), targets, 1)
    fetcher.content.update({BASE + name: content for name, content in metadata.items()})
    url = f"{RELEASES_URL}/download/v2.0.0/{wheel.name}"
    fetcher.content[url] = b"corrupt server package!!" if tamper else wheel.read_bytes()
    client = UpdateClient(cache=tmp_path / "cache", bootstrap=root_path.read_bytes(), metadata_url=BASE, fetcher=fetcher)
    release = client.check(target="server", current="1.0.0")
    assert release.available and release.target == "server"
    destination = tmp_path / "download.whl"
    if tamper:
        with pytest.raises(UpdateError):
            client.download(release, destination)
        assert not destination.exists()
    else:
        assert client.download(release, destination).read_bytes() == wheel.read_bytes()
        assert url in fetcher.urls


def test_server_feed_rejects_unverified_build_or_changed_wheel(tmp_path):
    wheel = tmp_path / "copydesk-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"synthetic package")
    report = {"version": "2.0.0", "archive": wheel.name, "commit": "a" * 40,
              "server_update_check": False, "package_install_check": True, "server_update_protocol": 1,
              "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="tested"):
        server_targets(tmp_path, report, "beta")
    report["server_update_check"] = True
    wheel.write_bytes(b"changed package")
    with pytest.raises(ValueError, match="changed"):
        server_targets(tmp_path, report, "beta")


def test_tampered_metadata_cannot_advertise_an_update(repository):
    client, fetcher, _publish = repository
    url = BASE + "1.targets.json"
    fetcher.content[url] = fetcher.content[url].replace(b"2.0.0", b"9.0.0")
    with pytest.raises(UpdateError, match="verified or reached"):
        client.check(target=TARGET, current="1.0.0")


def test_expired_metadata_is_rejected(repository):
    client, _fetcher, publish = repository
    publish(now=datetime.now(UTC) - timedelta(days=8))
    with pytest.raises(UpdateError, match="verified or reached"):
        client.check(target=TARGET, current="1.0.0")


def test_metadata_rollback_is_rejected_after_a_newer_check(repository):
    client, fetcher, publish = repository
    old = dict(fetcher.content)
    publish(version="3.0.0", sequence=2)
    assert client.check(target=TARGET, current="1.0.0").version == "3.0.0"
    fetcher.content = old
    with pytest.raises(UpdateError, match="verified or reached"):
        client.check(target=TARGET, current="1.0.0")


def test_download_tampering_preserves_installed_app_and_publication(repository, tmp_path):
    client, fetcher, _publish = repository
    installation = install_at(tmp_path / "installed")
    publication = tmp_path / "publication"
    publication.mkdir()
    (publication / "private.db").write_bytes(b"synthetic private content")
    update = client.check(target=TARGET, current="1.0.0")
    fetcher.content[f"{RELEASES_URL}/download/v2.0.0/{update.archive}"] = b"untrusted download"
    with pytest.raises(UpdateError, match="could not be downloaded"):
        client.prepare(update, installation, data_dir=publication)
    assert (installation / "QuillSail").read_text() == "old executable"
    assert (publication / "private.db").read_bytes() == b"synthetic private content"
    assert not list(tmp_path.glob(".quillsail-update-*"))


def test_prepare_stages_exact_signed_archive_without_changing_running_app(repository, tmp_path):
    client, _fetcher, _publish = repository
    installation = install_at(tmp_path / "installed")
    update = client.check(target=TARGET, current="1.0.0")
    plan_path = client.prepare(update, installation, data_dir=tmp_path / "publication", restart=False)
    plan = json.loads(plan_path.read_text())
    assert plan_path.stat().st_mode & 0o077 == 0
    assert plan["restart"] is False and plan["parent_pid"] == os.getpid()
    assert (installation / "QuillSail").read_text() == "old executable"
    update_install.atomic_install(plan)
    assert (installation / "QuillSail").read_text() == "new executable"
    assert (plan_path.parent / "previous/QuillSail").read_text() == "old executable"


def test_changed_release_requires_a_new_check_before_download(repository, tmp_path):
    client, _fetcher, publish = repository
    update = client.check(target=TARGET, current="1.0.0")
    publish(version="3.0.0", sequence=2)
    with pytest.raises(UpdateError, match="available update changed"):
        client.prepare(update, install_at(tmp_path / "installed"), data_dir=tmp_path / "publication")


def test_update_must_not_replace_a_tree_containing_publication_data(repository, tmp_path):
    client, _fetcher, _publish = repository
    installation = install_at(tmp_path / "installed")
    update = client.check(target=TARGET, current="1.0.0")
    with pytest.raises(UpdateError, match="publication data outside"):
        client.prepare(update, installation, data_dir=installation / "publication")


@pytest.mark.parametrize("name", ["../outside", "/absolute", "QuillSail/../../outside", "QuillSail\\outside", "QuillSail/file:stream", "other/file", "QuillSail/name.", "QuillSail/CON", "QuillSail/./thing", "QuillSail//thing"])
def test_unsafe_archive_names_are_rejected(tmp_path, name):
    archive = tmp_path / "update.zip"
    archive.write_bytes(bundle_bytes(extra={name: b"unsafe"}))
    with pytest.raises(update_install.InstallError):
        update_install.extract_archive(archive, tmp_path / "extract", TARGET)
    assert not (tmp_path / "outside").exists()


def test_archive_symlinks_cannot_escape_the_application(tmp_path):
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as output:
        item = zipfile.ZipInfo("QuillSail/escape")
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(item, "../../outside")
    with pytest.raises(update_install.InstallError, match="outside"):
        update_install.extract_archive(archive, tmp_path / "extract", TARGET)


def test_duplicate_archive_paths_and_expansion_limits_are_rejected(tmp_path, monkeypatch):
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("QuillSail/One", "one")
        output.writestr("QuillSail/one", "other")
    with pytest.raises(update_install.InstallError, match="duplicate"):
        update_install.extract_archive(archive, tmp_path / "extract", TARGET)
    monkeypatch.setattr(update_install, "MAX_EXPANDED", 2)
    with pytest.raises(update_install.InstallError, match="too large"):
        update_install.extract_archive(archive, tmp_path / "small", TARGET)


def test_replacement_failure_restores_the_previous_app(repository, tmp_path, monkeypatch):
    client, _fetcher, _publish = repository
    installation = install_at(tmp_path / "installed")
    plan_path = client.prepare(client.check(target=TARGET, current="1.0.0"), installation, data_dir=tmp_path / "publication")
    original_replace = os.replace

    def fail_new_move(source, destination):
        if "unpacked" in Path(source).parts:
            raise PermissionError("synthetic replacement failure")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_new_move)
    with pytest.raises(PermissionError):
        update_install.atomic_install(json.loads(plan_path.read_text()))
    assert (installation / "QuillSail").read_text() == "old executable"


def test_helper_rechecks_archive_hash_and_installation_version(repository, tmp_path):
    client, _fetcher, _publish = repository
    installation = install_at(tmp_path / "installed")
    plan_path = client.prepare(client.check(target=TARGET, current="1.0.0"), installation, data_dir=tmp_path / "publication")
    plan = json.loads(plan_path.read_text())
    archive = plan_path.parent / "update.archive"
    archive.write_bytes(archive.read_bytes().replace(b"new executable", b"bad executable"))
    with pytest.raises(update_install.InstallError, match="checksum"):
        update_install.atomic_install(plan)
    assert (installation / "QuillSail").read_text() == "old executable"


def test_python_install_is_not_silently_overwritten(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(UpdateError, match="--app"):
        installation_path()


def test_cli_check_and_declined_install_never_launch_an_installer(repository, tmp_path, monkeypatch):
    client, _fetcher, _publish = repository
    monkeypatch.setattr("copydesk.updater.UpdateClient", lambda: client)
    monkeypatch.setattr("copydesk.updater.native_target", lambda: TARGET)
    monkeypatch.setattr("copydesk.updater.launch_installer", lambda _plan: pytest.fail("installer unexpectedly launched"))
    runner = CliRunner()
    result = runner.invoke(main, ["update", "--desktop", "--check"])
    assert result.exit_code == 0 and "2.0.0 is available" in result.output
    installation = install_at(tmp_path / "installed")
    result = runner.invoke(main, ["update", "--app", str(installation)], input="n\n")
    assert result.exit_code == 1 and "Aborted" in result.output


def test_signing_keys_are_private_and_root_needs_two_signatures(tmp_path):
    keys_path, root_path = tmp_path / "keys.json", tmp_path / "root.json"
    initialize(keys_path, root_path)
    assert keys_path.stat().st_mode & 0o077 == 0
    root = Metadata.from_file(str(root_path))
    assert root.signed.roles["root"].threshold == 2
    assert len(root.signatures) == 3
    with pytest.raises(ValueError, match="without overwriting"):
        initialize(keys_path, root_path)


def test_expired_feed_can_be_authenticated_and_renewed_by_the_maintainer(repository):
    client, fetcher, publish = repository
    metadata = publish(now=datetime.now(UTC) - timedelta(days=8))
    root = Metadata.from_bytes(client.bootstrap)
    targets, version = read_published_metadata(root, metadata.__getitem__)
    assert version == 1 and f"beta/{TARGET}" in targets
    tampered = dict(metadata)
    tampered["1.targets.json"] = tampered["1.targets.json"].replace(b"2.0.0", b"9.0.0")
    with pytest.raises(LengthOrHashMismatchError):
        read_published_metadata(root, tampered.__getitem__)
    publish(sequence=2)
    assert client.check(target=TARGET, current="1.0.0").available


def test_external_cli_update_installs_synchronously(repository, tmp_path, monkeypatch):
    client, _fetcher, _publish = repository
    installation = install_at(tmp_path / "installed")
    monkeypatch.setattr("copydesk.updater.UpdateClient", lambda: client)
    monkeypatch.setattr("copydesk.updater.launch_installer", lambda _plan: pytest.fail("external CLI should install synchronously"))
    result = CliRunner().invoke(main, ["update", "--app", str(installation), "--yes"])
    assert result.exit_code == 0, result.output
    assert "Installed QuillSail 2.0.0" in result.output
    assert (installation / "QuillSail").read_text() == "new executable"


def test_open_application_is_not_replaced_by_cli(repository, tmp_path, monkeypatch):
    from copydesk.updater import install_lock_path

    client, _fetcher, _publish = repository
    installation = install_at(tmp_path / "installed")
    monkeypatch.setattr("copydesk.updater.UpdateClient", lambda: client)
    with update_install.application_lock(install_lock_path(installation)):
        result = CliRunner().invoke(main, ["update", "--app", str(installation), "--yes"])
    assert result.exit_code == 1 and "Close all QuillSail" in result.output
    assert (installation / "QuillSail").read_text() == "old executable"


def test_channel_preference_is_retained_and_invalid_values_are_ignored(tmp_path, monkeypatch):
    from copydesk.updater import save_channel, saved_channel

    monkeypatch.setattr("copydesk.updater.cache_dir", lambda: tmp_path)
    assert saved_channel() == "beta"
    save_channel("stable")
    assert saved_channel() == "stable"
    assert (tmp_path / "preferences.json").stat().st_mode & 0o077 == 0
    (tmp_path / "preferences.json").write_text('{"channel":"untrusted"}')
    assert saved_channel() == "beta"


def test_success_retains_one_previous_app_and_preserves_failed_or_unrelated_transactions(tmp_path):
    installation = tmp_path / "QuillSail"
    installation.mkdir()
    transactions = []
    for name, owner, status in [("old", installation, "installed"), ("failed", installation, "failed"),
                                ("unrelated", tmp_path / "OtherApp", "installed"), ("current", installation, "installed")]:
        transaction = tmp_path / (".quillsail-update-" + name)
        transaction.mkdir()
        (transaction / "plan.json").write_text(json.dumps({"transaction": str(transaction), "installation": str(owner)}))
        (transaction / "result.json").write_text(json.dumps({"status": status}))
        transactions.append(transaction)
    old, failed, unrelated, current = transactions
    (current / "update.archive").write_bytes(b"disposable completed archive")
    update_install.record_success({"transaction": str(current), "version": "2.0.0"}, installation)
    assert not old.exists()
    assert failed.exists() and unrelated.exists() and current.exists()
    assert not (current / "update.archive").exists()


def test_cleanup_never_deletes_current_transaction_through_a_path_alias(tmp_path):
    installation = tmp_path / "QuillSail"
    installation.mkdir()
    current = tmp_path / ".quillsail-update-current"
    current.mkdir()
    (current / "previous").mkdir()
    (current / "previous/old-app").write_text("synthetic previous version")
    (tmp_path / "detour").mkdir()
    alias = tmp_path / "detour" / ".." / current.name
    plan = {"transaction": str(alias), "installation": str(installation), "version": "2.0.0"}
    (current / "plan.json").write_text(json.dumps(plan))
    update_install.record_success(plan, installation)
    assert (current / "previous/old-app").read_text() == "synthetic previous version"
    assert json.loads((current / "result.json").read_text())["status"] == "installed"


@pytest.mark.parametrize("trusted", [True, False])
def test_update_https_uses_bundled_ca_without_system_roots(tmp_path, monkeypatch, trusted):
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from urllib3.exceptions import MaxRetryError, SSLError

    from copydesk import updater

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "test-ca.pem", tmp_path / "test-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    empty_ca_dir = tmp_path / "empty-ca"
    empty_ca_dir.mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-build-machine-ca.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(empty_ca_dir))
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    assert not ssl.create_default_context().get_ca_certs()
    if trusted:
        monkeypatch.setattr(updater.certifi, "where", lambda: str(cert_path))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"synthetic update metadata")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fetcher = updater.ProgressFetcher()
        url = f"https://localhost:{server.server_port}/timestamp.json"
        if trusted:
            assert b"".join(fetcher.fetch(url)) == b"synthetic update metadata"
        else:
            with pytest.raises(MaxRetryError) as error:
                b"".join(fetcher.fetch(url))
            assert isinstance(error.value.reason, SSLError)
            assert "CERTIFICATE_VERIFY_FAILED" in str(error.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
