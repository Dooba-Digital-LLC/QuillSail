"""Consistent, private database snapshots for managed server updates."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import inspect, text

from copydesk.db import make_engine
from copydesk.updater import UpdateError


class DatabaseBackup:
    def __init__(self, config, run):
        self.config, self.run = config, run
        self.engine = make_engine(config.database_url)
        self.url = self.engine.url
        self.kind = self.engine.dialect.name
        if self.kind not in {"sqlite", "postgresql", "mysql"}:
            raise UpdateError("Server updates support SQLite, PostgreSQL and MySQL/MariaDB databases.")

    def close(self):
        self.engine.dispose()

    def estimated_size(self):
        if self.kind == "sqlite":
            return Path(self.url.database).stat().st_size
        query = ("SELECT pg_database_size(current_database())" if self.kind == "postgresql" else
                 "SELECT COALESCE(SUM(data_length + index_length), 0) FROM information_schema.tables WHERE table_schema = DATABASE()")
        with self.engine.connect() as connection:
            return int(connection.execute(text(query)).scalar() or 0)

    def preflight(self):
        if self.kind == "sqlite":
            if not self.url.database or self.url.database == ":memory:":
                raise UpdateError("Server updates require a persistent database.")
            return
        if self.kind == "postgresql" and any("\n" in str(value) or "\r" in str(value) for value in
                                               (self.url.username, self.url.password, self.url.host, self.url.database)):
            raise UpdateError("PostgreSQL backup credentials must not contain line breaks.")
        programs = ("pg_dump", "pg_restore") if self.kind == "postgresql" else ("mariadb-dump|mysqldump", "mariadb|mysql")
        for choices in programs:
            if not any(shutil.which(name) for name in choices.split("|")):
                raise UpdateError(f"Install the database client utility {choices} before updating.")
        # Reject connection options that native utilities cannot safely mirror.
        supported = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name"} if self.kind == "postgresql" else {
            "charset", "unix_socket", "ssl_ca", "ssl_cert", "ssl_key", "ssl_verify_cert", "ssl_verify_identity", "connect_timeout"}
        if set(self.url.query) - supported:
            raise UpdateError("The database URL has connection options that need a supported native-backup mapping before automatic updates.")

    @contextlib.contextmanager
    def connection_options(self, folder):
        """Passwords are private temporary files, never process arguments."""
        descriptor, filename = tempfile.mkstemp(prefix=".database-auth-", dir=folder)
        os.chmod(filename, 0o600)
        try:
            if self.kind == "postgresql":
                def escape(value):
                    return str(value or "").replace("\\", "\\\\").replace(":", "\\:")
                fields = [self.url.host or "localhost", self.url.port or 5432, self.url.database,
                          self.url.username, self.url.password]
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(":".join(escape(value) for value in fields) + "\n")
                environment = {**{key: value for key, value in os.environ.items() if not key.startswith("PG")},
                               "PGPASSFILE": filename, "PGHOST": self.url.host or "localhost",
                               "PGPORT": str(self.url.port or 5432), "PGUSER": self.url.username or "",
                               "PGDATABASE": self.url.database or ""}
                for option, value in self.url.query.items():
                    environment["PGAPPNAME" if option == "application_name" else "PG" + option.upper()] = value
                yield [], environment
            else:
                def quote(value):
                    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'
                options = {"user": self.url.username, "password": self.url.password,
                           "host": self.url.host or "localhost", "port": self.url.port or 3306,
                           "protocol": "TCP", "default-character-set": self.url.query.get("charset", "utf8mb4")}
                for source, destination in {"unix_socket": "socket", "ssl_ca": "ssl-ca", "ssl_cert": "ssl-cert",
                                            "ssl_key": "ssl-key", "connect_timeout": "connect-timeout"}.items():
                    if source in self.url.query:
                        options[destination] = self.url.query[source]
                if "unix_socket" in self.url.query:
                    options["protocol"] = "SOCKET"
                if any(str(self.url.query.get(key, "")).lower() in {"true", "1"} for key in ("ssl_verify_cert", "ssl_verify_identity")):
                    if shutil.which("mariadb"):
                        options["ssl-verify-server-cert"] = "1"
                    else:
                        options["ssl-mode"] = "VERIFY_IDENTITY"
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write("[client]\n" + "\n".join(f"{key}={quote(value)}" for key, value in options.items()) + "\n")
                yield ["--defaults-file=" + filename], {key: value for key, value in os.environ.items() if key != "MYSQL_PWD"}
        finally:
            Path(filename).unlink(missing_ok=True)

    def snapshot(self, folder: Path):
        self.preflight()
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        if "jobs" in tables:
            with self.engine.connect() as connection:
                if connection.execute(text("SELECT count(*) FROM jobs WHERE status='running'")).scalar():
                    raise UpdateError("A job is still marked as running. Resolve its delivery state before updating.")
        connection = self.url.set(password=None).render_as_string(hide_password=True)
        manifest = {"kind": self.kind, "tables": tables, "connection": hashlib.sha256(connection.encode()).hexdigest()}
        if self.kind == "sqlite":
            with contextlib.closing(sqlite3.connect(self.url.database)) as source, contextlib.closing(sqlite3.connect(folder / "database.sqlite3")) as destination:
                source.backup(destination)
                destination.execute("PRAGMA journal_mode=DELETE")
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise UpdateError("The database backup did not pass its integrity check.")
            os.chmod(folder / "database.sqlite3", 0o600)
        else:
            with self.connection_options(folder) as (options, environment):
                if self.kind == "postgresql":
                    manifest["sequences"] = inspector.get_sequence_names()
                    self.run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--schema=public",
                              "--file", str(folder / "database.dump")], env=environment)
                    self.run(["pg_restore", "--list", str(folder / "database.dump")], env=environment)
                else:
                    program = shutil.which("mariadb-dump") or shutil.which("mysqldump")
                    with (folder / "database.dump").open("wb") as output:
                        self.run([program, *options, "--single-transaction", "--routines", "--events", "--triggers",
                                  "--hex-blob", "--no-tablespaces", self.url.database], env=environment, stdout=output)
                os.chmod(folder / "database.dump", 0o600)
        (folder / "database.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def restore(self, folder: Path):
        self.preflight()
        manifest = json.loads((folder / "database.json").read_text(encoding="utf-8"))
        if manifest["kind"] != self.kind:
            raise UpdateError("The backup belongs to a different database backend.")
        connection = self.url.set(password=None).render_as_string(hide_password=True)
        if manifest.get("connection") != hashlib.sha256(connection.encode()).hexdigest():
            raise UpdateError("The database connection changed since this backup. Restore with the matching configuration.")
        if self.kind == "sqlite":
            self.engine.dispose()
            database = Path(self.url.database)
            for suffix in ("-wal", "-shm"):
                Path(str(database) + suffix).unlink(missing_ok=True)
            shutil.copy2(folder / "database.sqlite3", database)
            return
        # Native --clean restores existing objects. Also remove tables/sequences
        # introduced by the failed migration so no future schema is left behind.
        inspector = inspect(self.engine)
        extra = set(inspector.get_table_names()) - set(manifest["tables"])
        with self.engine.begin() as connection:
            if self.kind == "mysql":
                connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for name in extra:
                quoted = self.engine.dialect.identifier_preparer.quote(name)
                connection.execute(text(f"DROP TABLE {quoted}" + (" CASCADE" if self.kind == "postgresql" else "")))
            if self.kind == "mysql":
                connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            else:
                for name in set(inspector.get_sequence_names()) - set(manifest.get("sequences", [])):
                    connection.execute(text("DROP SEQUENCE " + self.engine.dialect.identifier_preparer.quote(name) + " CASCADE"))
        with self.connection_options(folder) as (options, environment):
            if self.kind == "postgresql":
                self.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--exit-on-error",
                          "--dbname", self.url.database, str(folder / "database.dump")], env=environment)
            else:
                program = shutil.which("mariadb") or shutil.which("mysql")
                with (folder / "database.dump").open("rb") as source:
                    self.run([program, *options, "--binary-mode", self.url.database], env=environment, stdin=source)
