"""Disposable command installations; never write the host's PATH directories."""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from copydesk.cli import main
from copydesk.server_command import CommandLink, command_target
from copydesk.server_update import ServerProfile, private_json
from copydesk.updater import UpdateError


@pytest.fixture
def server(tmp_path, monkeypatch):
    root, instance, binary = tmp_path / "app", tmp_path / "instance", tmp_path / "bin"
    executable = root / "releases/initial/venv/bin/quillsail"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (root / "current").symlink_to(root / "releases/initial")
    instance.mkdir()
    (instance / "copydesk.toml").write_text('[site]\nsecret_key="synthetic-command-installation-secret-12345678"\nbase_url="http://localhost"\n[mail]\nbackend="file"\n')
    binary.mkdir()
    monkeypatch.setenv("PATH", str(binary))
    monkeypatch.setattr("copydesk.server_command.require_operator", lambda: None)
    profile = ServerProfile(tmp_path / "server.json", {
        "format": "quillsail.server-update.v1", "kind": "python", "install_root": str(root),
        "instance_root": str(instance), "config": str(instance / "copydesk.toml"),
        "backup_root": str(tmp_path / "backups"), "service_user": "synthetic-user",
        "web_service": "synthetic-web", "worker_service": "synthetic-worker", "health_url": "http://localhost/healthz",
    })
    return profile, binary / "quillsail"


def register(profile, destination, *options):
    return CliRunner().invoke(main, ["update", "setup", "--profile", str(profile.path),
        "--install-root", str(profile.at("install_root")), "--instance-root", str(profile.at("instance_root")),
        "--backup-root", str(profile.at("backup_root")), "--command", str(destination), "--yes", *options])


def test_setup_installs_a_command_that_follows_future_releases(server):
    profile, destination = server
    result = register(profile, destination)
    assert result.exit_code == 0, result.output
    assert destination.is_symlink() and Path(os.readlink(destination)) == command_target(profile)
    assert "sudo quillsail update --profile" in result.output
    assert json.loads(profile.path.read_text())["command_path"] == str(destination)
    next_release = profile.at("install_root") / "releases/next"
    (next_release / "venv/bin").mkdir(parents=True)
    (next_release / "venv/bin/quillsail").write_text("next release")
    current = profile.at("install_root") / "current"
    current.unlink()
    current.symlink_to(next_release)
    assert destination.read_text() == "next release"


@pytest.mark.parametrize("existing", ["file", "foreign-link", "pinned-release-link"])
def test_setup_refuses_conflicting_or_pinned_commands_without_creating_a_profile(server, existing):
    profile, destination = server
    if existing == "file":
        destination.write_text("another tool")
    elif existing == "foreign-link":
        destination.symlink_to(destination.parent / "missing-foreign-tool")
    else:
        destination.symlink_to(command_target(profile).resolve())
    before = destination.lstat()
    result = register(profile, destination)
    assert result.exit_code == 1 and "not replaced" in result.output
    assert destination.lstat().st_ino == before.st_ino and not profile.path.exists()


def test_setup_refuses_an_unreachable_command_directory(server, monkeypatch):
    profile, destination = server
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    result = register(profile, destination)
    assert result.exit_code == 1 and "not on this sudo session" in result.output
    assert not destination.exists() and not profile.path.exists()


def test_setup_refuses_commands_shadowed_by_an_earlier_path_entry(server, tmp_path, monkeypatch):
    profile, destination = server
    first = tmp_path / "first"
    first.mkdir()
    (first / destination.name).write_text("earlier tool")
    (first / destination.name).chmod(0o755)
    monkeypatch.setenv("PATH", str(first) + os.pathsep + str(destination.parent))
    result = register(profile, destination)
    assert result.exit_code == 1 and "shadow" in result.output
    assert not destination.exists() and not profile.path.exists()


def test_setup_can_explicitly_preserve_an_existing_global_command(server):
    profile, destination = server
    destination.write_text("another tool")
    result = register(profile, destination, "--no-command")
    assert result.exit_code == 0, result.output
    assert destination.read_text() == "another tool" and profile.path.exists()
    assert "command_path" not in json.loads(profile.path.read_text())


def test_registration_cannot_pin_the_command_directory_inside_a_release(server, monkeypatch):
    profile, _destination = server
    destination = command_target(profile).resolve()
    monkeypatch.setenv("PATH", str(destination.parent))
    result = register(profile, destination)
    assert result.exit_code == 1 and "outside the versioned releases" in result.output
    assert not profile.path.exists()


def test_registration_failure_removes_only_its_own_new_command(server, monkeypatch):
    profile, destination = server

    def fail(*args):
        raise OSError("synthetic read-only profile folder")

    monkeypatch.setattr("copydesk.server_update.private_json", fail)
    result = register(profile, destination)
    assert result.exit_code == 1 and not destination.exists() and not profile.path.exists()


def test_existing_servers_can_install_and_verify_the_same_command_repeatedly(server):
    profile, destination = server
    private_json(profile.path, profile.values)
    arguments = ["update", "install-command", "--profile", str(profile.path), "--command", str(destination)]
    first = CliRunner().invoke(main, arguments)
    assert first.exit_code == 0, first.output
    identity = destination.lstat().st_ino
    second = CliRunner().invoke(main, arguments)
    assert second.exit_code == 0 and destination.lstat().st_ino == identity
    assert "sudo quillsail update --profile" in second.output


def test_command_installation_does_not_overwrite_a_file_claimed_after_validation(server):
    profile, destination = server
    link = CommandLink(destination, command_target(profile))
    link.validate()
    destination.write_text("another administrator's command")
    with pytest.raises(UpdateError, match="not replaced"):
        link.install()
    assert destination.read_text() == "another administrator's command"


def test_untrusted_or_relative_path_entries_are_not_accepted_as_a_lookup_guarantee(server, tmp_path, monkeypatch):
    profile, destination = server
    link = CommandLink(destination, command_target(profile))
    with pytest.raises(UpdateError, match="absolute"):
        link.validate(search_path=".:" + str(destination.parent))
    first = tmp_path / "shared"
    first.mkdir(mode=0o777)
    first.chmod(0o777)
    monkeypatch.setenv("PATH", str(first) + os.pathsep + str(destination.parent))
    with pytest.raises(UpdateError, match="protected"):
        link.validate()


def test_compose_reuses_a_persistent_host_command_without_replacing_its_entrypoint(server):
    profile, destination = server
    destination.write_text("#!/bin/sh\nexit 0\n")
    destination.chmod(0o755)
    profile.values.update(kind="compose", command_target=str(destination))
    link = CommandLink(destination, command_target(profile))
    identity = destination.lstat().st_ino
    link.install()
    assert not destination.is_symlink() and destination.lstat().st_ino == identity


@pytest.mark.parametrize("existing_profile", [False, True])
def test_bootstrap_installs_or_checks_the_stable_command_before_upgrading(tmp_path, monkeypatch, existing_profile):
    import hashlib
    import importlib.util
    import subprocess
    import sys

    path = Path(__file__).resolve().parents[1] / "deploy/bootstrap_server.py"
    spec = importlib.util.spec_from_file_location("bootstrap_command_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wheel = tmp_path / "copydesk-1.9.1-py3-none-any.whl"
    wheel.write_bytes(b"synthetic pinned installer")
    profile = tmp_path / "update.json"
    if existing_profile:
        profile.write_text('{"kind":"python"}')
    arguments = [str(path), "--wheel", str(wheel), "--sha256", hashlib.sha256(wheel.read_bytes()).hexdigest(),
                 "--profile", str(profile), "--yes"]
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    calls = []

    def run(arguments, **options):
        calls.append(list(map(str, arguments)))
        return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps({"system_command": True}))

    monkeypatch.setattr(module.subprocess, "run", run)
    module.main()
    link = next(index for index, call in enumerate(calls) if "install-command" in call)
    upgrade = next(index for index, call in enumerate(calls) if "bootstrap" in call)
    assert link < upgrade
    assert any("setup" in call for call in calls) is not existing_profile
