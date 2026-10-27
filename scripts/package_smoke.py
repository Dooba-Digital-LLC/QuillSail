"""Install a built wheel in a clean environment and check a fresh blog instance."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main(wheel):
    wheel = Path(wheel).resolve()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COPYDESK_", "GOOGLE_", "MICROSOFT_")) and key != "PYTHONPATH"
    }
    with tempfile.TemporaryDirectory(prefix="copydesk-package-") as folder:
        root = Path(folder)
        subprocess.run(
            [sys.executable, "-m", "venv", str(root / "venv")], check=True, env=environment
        )
        python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        command = root / "venv" / ("Scripts/copydesk.exe" if os.name == "nt" else "bin/copydesk")
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                str(wheel) + "[server]",
            ],
            check=True,
            cwd=root,
            env=environment,
        )
        subprocess.run(
            [
                str(command),
                "init",
                "--dir",
                str(root / "instance"),
                "--mode",
                "blog",
                "--site-name",
                "Package smoke blog",
                "--base-url",
                "http://localhost/community",
                "--admin-name",
                "Sample Owner",
                "--admin-email",
                "owner@example.test",
                "--admin-password",
                "synthetic-smoke-password-123",
                "--yes",
            ],
            check=True,
            cwd=root,
            env=environment,
        )
        code = """from pathlib import Path
import copydesk
from copydesk.app import create_app
from copydesk.config import load_config
assert Path(copydesk.__file__).resolve().is_relative_to(Path('venv').resolve())
config = Path('instance/copydesk.toml')
assert config.stat().st_mode & 0o077 == 0
app = create_app(load_config(config))
with app.test_client() as client:
    assert client.get('/community/healthz').get_json()['database'] == 'ok'
    assert b'Latest posts' in client.get('/community/').data
    assert client.get('/community/static/css/copydesk.css').status_code == 200
    assert client.get('/community/static/brand/quillsail.svg').mimetype == 'image/svg+xml'
    assert client.get('/community/static/brand/quillsail.png').data.startswith(b'\\x89PNG')
print('Clean wheel installation, migrations, blog setup, subpath routing and assets passed.')
"""
        subprocess.run([str(python), "-c", code], check=True, cwd=root, env=environment)


if __name__ == "__main__":
    main(sys.argv[1])
