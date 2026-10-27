"""Disposable instance updates: no production services, accounts or real mail."""

import json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from copydesk import jobs, mail
from copydesk.cli import main
from copydesk.maintenance import marker
from copydesk.server_update import ServerProfile, ServerUpdater, private_json
from copydesk.updater import Update, UpdateError


class FakeClient:
    def download(self, update, destination):
        destination.write_bytes(b"synthetic verified wheel")
        return destination


class Deployment:
    def __init__(self, profile, *, fail=None):
        self.profile, self.fail, self.calls = profile, fail, []
        self.current = profile.at("install_root") / "current"

    def version(self):
        return (self.current.resolve() / "version").read_text()

    def preflight(self):
        self.calls.append("preflight")

    def prepare(self, wheel, version, transaction):
        self.calls.append("prepare")
        if self.fail == "prepare":
            raise UpdateError("synthetic prepare failure")
        release = self.current.parent / "releases" / version
        release.mkdir()
        (release / "version").write_text(version)
        return {"old_release": str(self.current.resolve()), "new_release": str(release)}

    def stop(self):
        self.calls.append("stop")

    def activate(self, state, previous=False):
        self.calls.append("activate-old" if previous else "activate-new")
        self.current.unlink()
        self.current.symlink_to(state["old_release" if previous else "new_release"])

    def migrate(self):
        self.calls.append("migrate")
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE future_schema (id INTEGER)")
            connection.execute("UPDATE sentinel SET value='new version'")
        if self.fail == "migration":
            raise UpdateError("synthetic migration failure")

    @property
    def database(self):
        return self.profile.at("instance_root") / "data/copydesk.db"

    def start(self, worker=False):
        self.calls.append("start-worker" if worker else "start-web")
        if worker:
            self.calls.append("worker-paused" if marker(self.profile.config()).exists() else "worker-enabled")
            with sqlite3.connect(self.database) as connection:
                connection.execute("UPDATE settings SET value=? WHERE key='system.worker_identity'", (json.dumps("new-worker"),))

    def worker_active(self):
        return True

    def ownership(self):
        return os.geteuid(), os.getegid()


@pytest.fixture
def deployment(tmp_path):
    root, instance, backups = tmp_path / "application", tmp_path / "instance", tmp_path / "backups"
    (root / "releases/1.0.0").mkdir(parents=True)
    (root / "releases/1.0.0/version").write_text("1.0.0")
    (root / "current").symlink_to(root / "releases/1.0.0")
    (instance / "data/uploads").mkdir(parents=True)
    (instance / "data/uploads/logo.txt").write_text("synthetic logo")
    (instance / "copydesk.toml").write_text('[site]\nsecret_key="synthetic-server-update-secret-at-least-32-characters"\nbase_url="http://localhost/publication"\n[mail]\nbackend="file"\n')
    with sqlite3.connect(instance / "data/copydesk.db") as connection:
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value JSON, updated_at TEXT)")
        connection.execute("INSERT INTO settings(key,value) VALUES ('system.worker_identity',?)", (json.dumps("old-worker"),))
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('old version')")
        connection.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
        connection.execute("INSERT INTO jobs VALUES (1,'queued')")
    profile_path = tmp_path / "update.json"
    private_json(profile_path, {"format": "quillsail.server-update.v1", "kind": "python", "install_root": str(root),
                               "instance_root": str(instance), "config": str(instance / "copydesk.toml"),
                               "backup_root": str(backups), "service_user": "synthetic-user", "web_service": "synthetic-web",
                               "worker_service": "synthetic-worker", "health_url": "http://localhost/publication/healthz", "health_timeout": 0.05})
    profile = ServerProfile.load(profile_path)
    provider = Deployment(profile)
    controller = ServerUpdater(profile, client=FakeClient(), deployment=provider, require_privileges=False,
                               health=lambda version: provider.fail != "health")
    release = Update("1.0.0", "2.0.0", "server", "beta", "copydesk-2.0.0-py3-none-any.whl", 24, "a" * 64)
    return controller, provider, release


def value(provider):
    with sqlite3.connect(provider.database) as connection:
        return connection.execute("SELECT value FROM sentinel").fetchone()[0]


def test_successful_update_prepares_before_downtime_and_validates_paused_worker(deployment):
    controller, provider, release = deployment
    state = controller.execute(release)
    assert state["stage"] == "complete"
    assert provider.version() == "2.0.0" and value(provider) == "new version"
    assert provider.calls.index("prepare") < provider.calls.index("stop") < provider.calls.index("migrate")
    assert "worker-paused" in provider.calls and "worker-enabled" not in provider.calls
    assert not marker(controller.config).exists()
    assert (Path(state["transaction"]) / "snapshot/database.sqlite3").exists()
    assert (Path(state["transaction"]) / "snapshot").stat().st_mode & 0o077 == 0
    with sqlite3.connect(provider.database) as connection:
        assert connection.execute("SELECT status FROM jobs").fetchone()[0] == "queued"


@pytest.mark.parametrize("failure", ["migration", "health"])
def test_failure_restores_matching_application_database_and_uploads(deployment, failure):
    controller, provider, release = deployment
    provider.fail = failure
    with pytest.raises(UpdateError):
        controller.execute(release)
    assert provider.version() == "1.0.0" and value(provider) == "old version"
    assert controller.state()["stage"] == "rolled_back"
    assert not marker(controller.config).exists()
    assert (controller.profile.at("instance_root") / "data/uploads/logo.txt").read_text() == "synthetic logo"
    with sqlite3.connect(provider.database) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='future_schema'").fetchone()


def test_preparation_failure_keeps_services_and_data_online(deployment):
    controller, provider, release = deployment
    provider.fail = "prepare"
    with pytest.raises(UpdateError, match="prepare"):
        controller.execute(release)
    assert "stop" not in provider.calls and provider.version() == "1.0.0"
    controller.execute(resume=True)
    assert "stop" not in provider.calls and controller.state()["stage"] == "cancelled"


def test_active_jobs_prevent_migration_and_do_not_replay_delivery(deployment):
    controller, provider, release = deployment
    with sqlite3.connect(provider.database) as connection:
        connection.execute("UPDATE jobs SET status='running'")
    with pytest.raises(UpdateError, match="job is still marked"):
        controller.execute(release)
    assert "migrate" not in provider.calls and value(provider) == "old version"


def test_resume_after_commit_never_restores_a_database_snapshot(deployment):
    controller, provider, release = deployment
    state = controller.execute(release)
    with sqlite3.connect(provider.database) as connection:
        connection.execute("UPDATE sentinel SET value='new reader content'")
    controller.save(state, "committed")
    controller.execute(resume=True)
    assert value(provider) == "new reader content" and provider.version() == "2.0.0"
    assert controller.state()["stage"] == "complete"


def test_unsafe_instance_links_fail_before_database_migration(deployment, tmp_path):
    controller, provider, release = deployment
    external = tmp_path / "outside-secret"
    external.write_text("synthetic external data")
    (controller.profile.at("instance_root") / "link").symlink_to(external)
    with pytest.raises(UpdateError, match="filesystem links"):
        controller.execute(release)
    assert external.read_text() == "synthetic external data" and "migrate" not in provider.calls


def test_untrusted_profile_permissions_are_rejected(deployment):
    controller, _provider, _release = deployment
    os.chmod(controller.profile.path, 0o666)
    with pytest.raises(UpdateError, match="not be writable"):
        ServerProfile.load(controller.profile.path)


def test_maintenance_gate_blocks_requests_but_keeps_health_available(app, client):
    config = app.extensions["copydesk"].config
    marker(config).write_text('{"transaction":"synthetic"}')
    response = client.get("/")
    assert response.status_code == 503 and response.headers["Cache-Control"] == "no-store"
    assert client.post("/login", data={}).status_code == 503
    health = client.get("/healthz")
    assert health.status_code == 200 and health.json["maintenance"] is True
    marker(config).unlink()
    assert client.get("/").status_code == 200


def test_paused_worker_does_not_claim_or_send_jobs(app):
    from copydesk.db import db
    from copydesk.models import Job

    with app.app_context():
        queued = jobs.enqueue("synthetic-update-job", {})
        db.session.commit()
        marker(app.extensions["copydesk"].config).write_text('{"transaction":"synthetic"}')
        before = len(mail.outbox)
        assert jobs.run_one() is False
        jobs.heartbeat()
        assert db.session.get(Job, queued.id).status == "queued"
        assert len(mail.outbox) == before
        marker(app.extensions["copydesk"].config).unlink()


def test_capabilities_needs_no_instance_or_network():
    result = CliRunner().invoke(main, ["update", "capabilities"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["server_update_protocol"] == 1 and data["maintenance_gate"]


def test_manual_rollback_saves_current_content_before_restoring_previous_snapshot(deployment):
    controller, provider, release = deployment
    controller.execute(release)
    with sqlite3.connect(provider.database) as connection:
        connection.execute("UPDATE sentinel SET value='reader content after update'")
    state = controller.rollback()
    assert state["stage"] == "rolled_back" and provider.version() == "1.0.0"
    assert value(provider) == "old version"
    safety = Path(state["transaction"]) / "snapshot/database.sqlite3"
    with sqlite3.connect(safety) as connection:
        assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == "reader content after update"


def test_bootstrap_rejects_wrong_checksum_without_changing_server(deployment, tmp_path, monkeypatch):
    controller, provider, _release = deployment
    wheel = tmp_path / "copydesk-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"operator-provided synthetic package")
    monkeypatch.setattr("copydesk.update_commands.server_controller", lambda _profile: controller)
    result = CliRunner().invoke(main, ["update", "bootstrap", "--wheel", str(wheel), "--sha256", "0" * 64, "--yes"])
    assert result.exit_code == 1 and "checksum" in result.output
    assert not provider.calls and provider.version() == "1.0.0"


def test_server_cli_selects_registered_profile_and_checks_the_server_channel(deployment, monkeypatch):
    controller, provider, release = deployment
    observed = []

    class Client:
        def check(self, **options):
            observed.append(options)
            return release

    controller.client = Client()
    monkeypatch.setattr("copydesk.update_commands.server_controller", lambda _profile: controller)
    result = CliRunner().invoke(main, ["update", "--profile", str(controller.profile.path), "--check"])
    assert result.exit_code == 0, result.output
    assert observed == [{"channel": "beta", "target": "server", "current": "1.0.0"}]
    assert "stop" not in provider.calls


def test_profile_rejects_service_writable_backup_directory(deployment):
    controller, _provider, _release = deployment
    os.chmod(controller.profile.at("backup_root"), 0o777)
    with pytest.raises(UpdateError, match="protected"):
        ServerProfile.load(controller.profile.path)


def test_single_server_command_installs_the_checked_release(deployment, monkeypatch):
    controller, provider, release = deployment

    class Client(FakeClient):
        def check(self, **options):
            assert options["target"] == "server"
            return release

    controller.client = Client()
    monkeypatch.setattr("copydesk.update_commands.server_controller", lambda _profile: controller)
    result = CliRunner().invoke(main, ["update", "--profile", str(controller.profile.path), "--yes"])
    assert result.exit_code == 0, result.output
    assert provider.version() == "2.0.0" and "web and worker checks passed" in result.output


def test_manual_cli_rollback_requires_mail_replay_acknowledgement(deployment, monkeypatch):
    controller, provider, _release = deployment
    monkeypatch.setattr("copydesk.update_commands.server_controller", lambda _profile: controller)
    result = CliRunner().invoke(main, ["update", "rollback", "--yes"])
    assert result.exit_code == 1 and "acknowledge-mail-replay" in result.output
    assert not provider.calls


def test_profiles_cannot_update_the_same_installation_concurrently(deployment):
    controller, _provider, _release = deployment
    other = ServerProfile(controller.profile.path, {**controller.profile.values,
                          "backup_root": str(controller.profile.at("backup_root").parent / "other-backups")})
    second = ServerUpdater(other, require_privileges=False)
    with controller.lock(), pytest.raises(UpdateError, match="already running"), second.lock():
        pass


def test_insufficient_disk_space_does_not_stop_services(deployment, monkeypatch):
    from collections import namedtuple

    controller, provider, release = deployment
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("copydesk.server_update.shutil.disk_usage", lambda _path: usage(100, 99, 1))
    with pytest.raises(UpdateError, match="disk space"):
        controller.execute(release)
    assert "stop" not in provider.calls


def test_compose_candidate_build_contains_only_the_wheel_and_pins_recovery_images(deployment, tmp_path):
    from copydesk.compose_update import ComposeDeployment

    controller, _provider, _release = deployment
    profile = ServerProfile(controller.profile.path, {**controller.profile.values, "kind": "compose",
                            "compose_file": str(tmp_path / "compose.json"), "compose_project": "synthetic",
                            "base_image": "python@sha256:" + "a" * 64})
    (tmp_path / "compose.json").write_text('{"services":{}}')
    wheel = tmp_path / "copydesk-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"synthetic package")
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    calls = []

    def run(arguments, **options):
        arguments = list(map(str, arguments))
        calls.append(arguments)
        if arguments[:2] == ["docker", "run"]:
            return json.dumps({"version": "2.0.0", "server_update_protocol": 1, "maintenance_gate": True})
        if arguments[:2] == ["docker", "inspect"]:
            return "sha256:" + "b" * 64
        return "synthetic-container"

    adapter = ComposeDeployment(profile, run)
    state = adapter.prepare(wheel, "2.0.0", transaction)
    assert state["old_images"]["web"] == "sha256:" + "b" * 64
    assert {path.name for path in (transaction / "image-build").iterdir()} == {wheel.name, "Dockerfile"}
    assert "FROM python@sha256:" in (transaction / "image-build/Dockerfile").read_text()
    adapter.activate(state)
    assert json.loads(adapter.override.read_text())["services"]["worker"]["image"] == state["new_image"]
    adapter.activate(state, previous=True)
    assert json.loads(adapter.override.read_text())["services"]["web"]["image"] == state["old_images"]["web"]
    adapter.migrate()
    assert calls[-1][-4:] == ["run", "--rm", "--no-deps", "init"]
