"""Container lifecycle: explicit initialization/migrations, then web or worker."""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from string import Template

from copydesk import migrate
from copydesk.config import ConfigError, load_config
from copydesk.db import make_engine


def config_path():
    return Path(os.environ.get("COPYDESK_CONFIG", "/var/lib/copydesk/copydesk.toml"))


def initialize():
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        import copydesk

        source = Path(copydesk.__file__).parent / "config_template.toml"

        # The template placeholders sit inside TOML quoted strings.
        # JSON string escaping is compatible with TOML basic string escaping.
        def escape(value):
            return json.dumps(str(value), ensure_ascii=True)[1:-1]

        content = Template(source.read_text()).substitute(
            base_url=escape(os.environ.get("COPYDESK_BASE_URL", "http://localhost:8080")),
            secret_key=escape(os.environ.get("COPYDESK_SECRET_KEY") or secrets.token_hex(32)),
            database_url="sqlite:///data/copydesk.db",
            mail_backend="file",
            from_email="newsletter@localhost",
            proxy_hops="0",
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                handle.write(content)
            # Another initializer may win; never replace its secret key.
            with suppress(FileExistsError):
                os.link(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    config = load_config(path, require_file=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.uploads_dir.mkdir(parents=True, exist_ok=True)
    config.extensions_dir.mkdir(parents=True, exist_ok=True)
    config.themes_dir.mkdir(parents=True, exist_ok=True)
    config.mail.file_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(config.database_url)
    try:
        if engine.dialect.name == "sqlite" and engine.url.database not in {None, ":memory:"}:
            Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
        migrate.upgrade(engine)
    finally:
        engine.dispose()
    print("QuillSail initialized. Finish the first administrator setup in your browser.")


def require_current_schema():
    config = load_config(config_path(), require_file=True)
    engine = make_engine(config.database_url)
    try:
        if engine.dialect.name == "sqlite" and engine.url.database not in {None, ":memory:"} and not Path(engine.url.database).exists():
            raise ConfigError("Run the init service before starting web/worker. Back up first when upgrading.")
        if migrate.needs_upgrade(engine):
            raise ConfigError(
                "Run the init service before starting web/worker. Back up first when upgrading."
            )
    finally:
        engine.dispose()


def main(arguments=None):
    os.umask(0o077)
    os.environ.setdefault("COPYDESK_CONFIG", str(config_path()))
    arguments = sys.argv[1:] if arguments is None else arguments
    command = arguments[0] if arguments else "web"
    if command == "init":
        initialize()
        return
    if command in {"web", "worker"}:
        require_current_schema()
    if command == "web":
        workers = int(os.environ.get("COPYDESK_WEB_WORKERS", "2"))
        if not 1 <= workers <= 64:
            raise ConfigError("COPYDESK_WEB_WORKERS must be between 1 and 64.")
        os.execvp(
            "gunicorn",
            [
                "gunicorn",
                "--bind",
                "0.0.0.0:8000",
                "--workers",
                str(workers),
                "--access-logfile",
                "-",
                "--error-logfile",
                "-",
                "--worker-tmp-dir",
                "/tmp",
                "--access-logformat",
                "%(h)s %(m)s %(U)s %(s)s %(L)s",
                "copydesk.wsgi:app",
            ],
        )
    if command == "worker":
        os.execvp("quillsail", ["quillsail", "worker", *arguments[1:]])
    os.execvp("quillsail", ["quillsail", *arguments])


if __name__ == "__main__":
    try:
        main()
    except (ConfigError, ValueError) as error:
        print(f"QuillSail startup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
