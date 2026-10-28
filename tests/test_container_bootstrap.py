import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from sqlalchemy import func, select

from copydesk import migrate
from copydesk.app import create_app
from copydesk.config import ConfigError, load_config
from copydesk.db import db
from copydesk.models import User

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "deploy/container/entrypoint.py"


def container_env(path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COPYDESK_", "GOOGLE_", "MICROSOFT_"))
    }
    env["COPYDESK_CONFIG"] = str(path)
    env["COPYDESK_BASE_URL"] = "http://localhost:8080/community"
    return env


def test_container_initializer_is_private_idempotent_and_has_no_default_admin(tmp_path):
    config = tmp_path / "instance/copydesk.toml"
    env = container_env(config)
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "init"], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        content = config.read_bytes()
        data = tomllib.loads(content.decode())
        assert data["site"]["secret_key"] not in result.stdout + result.stderr
        assert config.stat().st_mode & 0o077 == 0
        if _ == 0:
            original = content
        else:
            assert content == original
    app = create_app(load_config(config))
    with app.app_context():
        assert migrate.current_revision(db.engine) == migrate.head_revision()
        assert db.session.scalar(select(func.count()).select_from(User)) == 0
        assert app.extensions["copydesk"].config.mail.backend == "file"
    client = app.test_client()
    assert client.get("/community/healthz").get_json()["database"] == "ok"
    setup = client.get("/community/setup/")
    assert setup.status_code == 200 and b"Setup code" in setup.data
    assert (config.parent / "data/setup-code.txt").stat().st_mode & 0o077 == 0
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_container_startup_refuses_missing_migrations(tmp_path, monkeypatch):
    config = tmp_path / "copydesk.toml"
    config.write_text('[site]\nsecret_key = "synthetic-container-secret-at-least-32-characters"\n')
    monkeypatch.setenv("COPYDESK_CONFIG", str(config))
    spec = importlib.util.spec_from_file_location("container_entrypoint_test", ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ConfigError, match="init service"):
        module.require_current_schema()


def test_container_initializer_does_not_replace_operator_configuration(tmp_path):
    config = tmp_path / "copydesk.toml"
    original = '[site]\nsecret_key = "existing-operator-secret-at-least-32-characters"\nbase_url = "http://localhost:8080"\n[mail]\nbackend = "memory"\n'
    config.write_text(original)
    config.chmod(0o600)
    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT), "init"],
        env=container_env(config),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert config.read_text() == original
