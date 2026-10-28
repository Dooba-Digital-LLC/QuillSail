# Managed server updates

The CLI manages the complete update of a registered Linux server: authenticated
release download, preparation, maintenance mode, backup, migration, activation,
web and worker checks, and recovery. Both versioned Python/systemd installations
and the supplied Docker Compose deployment are supported. Desktop installations
use the launcher's update button or `quillsail update --app /path/to/application`.

Once registered, a server needs one command:

```sh
sudo /opt/copydesk/current/venv/bin/quillsail update
```

If that executable is on your sudo PATH, use `sudo quillsail update`. The
`copydesk` command remains an alias. Add `--yes` for unattended approval or
`--check` to check availability without installing. The profile selects Stable
or Beta; `--channel stable` or `--channel beta` overrides it for one invocation.
An unpublished development version does not become available merely by being
committed to GitHub. Maintainers must publish its tested packages and signed
update metadata first.

## Register a Python/systemd server once

Install a release that includes server update support (1.9.0 or later). The
standard production layout is:

```text
/opt/copydesk/current -> /opt/copydesk/releases/VERSION
/opt/copydesk/releases/VERSION/venv
/var/lib/copydesk/copydesk.toml
/var/lib/copydesk/data/...
```

Application releases and the update profile must be root-owned, protected from
other users, and readable by the service account. Publication files belong to
the service account. The web and worker systemd units must use executables under
`/opt/copydesk/current/venv/bin/`. See the supplied [web](../deploy/systemd/copydesk-web.service)
and [worker](../deploy/systemd/copydesk-worker.service) examples.

```sh
sudo /opt/copydesk/current/venv/bin/quillsail update setup \
  --health-url http://127.0.0.1:8000/healthz
```

For a site under `/publication`, use `http://127.0.0.1:8000/publication/healthz`.
Specify the actual port. Options allow different installation/instance/backup
roots, service names and service account. Use `quillsail update setup --help`.
Setup records a protected `/etc/copydesk/update.json`; it does not replace live
code or stop services. Use `--profile /protected/path/update.json` for another
installation and pass that profile to subsequent update commands.

If systemd uses environment files, supply each one using
`--environment-file /etc/copydesk/production.env`. These files must be root-owned
and must not be writable by the service account. They use literal dotenv
`KEY=value` syntax without shell expansion. Register the same database and path
overrides used by the services. The updater deliberately ignores ambient
`COPYDESK_*` and Python import-path variables. Keep all mutable files, including
the SQLite database, uploads, themes, extensions and file mail, inside the
registered instance root. Filesystem links and special files in the instance
are rejected; move their targets inside the instance first.

### Bootstrap an older server

Older commands cannot acquire update support themselves. Obtain the new wheel,
its SHA-256 digest and `deploy/bootstrap_server.py` through a trusted first-install
channel. Review the installer and verify the digest independently, then run:

```sh
sudo python3 -I deploy/bootstrap_server.py \
  --wheel /protected/copydesk-VERSION-py3-none-any.whl \
  --sha256 TRUSTED_64_CHARACTER_SHA256 \
  --health-url http://127.0.0.1:8000/healthz
```

This one command stages the updater in a temporary environment, registers the
existing versioned installation, and runs the normal backed-up update
transaction. Adjust its path/service options for the existing installation.
Python 3.11+ and working package-index access are required. First installation
uses the operator's explicitly pinned local installer; later network updates
authenticate their metadata and wheel with TUF. There is no unsigned network
fallback.

## Register Docker Compose once

Run the updater on the Linux Docker host as root, using a host Python environment
with QuillSail 1.9.0+ installed. Keep that host tool available for future updates.
Start the existing web and worker containers, then register their project:

```sh
sudo /opt/quillsail-tools/bin/quillsail update setup \
  --kind compose --compose-file /srv/quillsail/compose.yaml \
  --project copydesk --install-root /opt/quillsail-compose \
  --health-url http://127.0.0.1:8080/healthz
sudo /opt/quillsail-tools/bin/quillsail update
```

Supply your actual Compose project name and settings. Setup records a private,
fully resolved Compose configuration, discovers the persistent
`/var/lib/copydesk` mount, and pins the Python base image by digest. Pass
`--base-image IMAGE@sha256:DIGEST` to choose that image explicitly. Environment
files can supply Compose interpolation values. After registration, use the
managed configuration for lifecycle commands: the original source Compose file
still describes its original image. Do not run two independent managers against
the same project. Inspect the protected profile for `compose_file`, project and
`install_root`; the updater's `current-image.json` override selects the active
images. Reregister into a new protected installation/profile when intentionally
changing the resolved deployment configuration.

The updater builds a local image from the verified release wheel before taking
the site offline. It preserves the old image IDs for recovery. It does not
publish an image or require a QuillSail image registry. The Docker daemon, pinned
base-image distribution and Python dependency index remain trusted installation
dependencies, as they are for Python deployments.

For an external database whose container hostname is unavailable on the host,
use `--backup-host HOST --backup-port PORT` with a host-reachable endpoint for
the **same database**. Keep it private. SQLite needs no external client.

## Backup and recovery

New packages/images are prepared while the current site stays online. Then
requests receive a short maintenance response, web and worker stop, and the
updater copies the complete instance and takes a consistent native database
snapshot. It checks backup hashes, runs migrations, starts the new web service,
checks the exact version and database, and starts the worker while queued work
is still paused. A fresh worker identity is required before committing and
reopening the site. No test email is sent.

For PostgreSQL, install `pg_dump` matching the server's major version (or newer)
and `pg_restore`. PostgreSQL updates manage the application's dedicated `public`
schema. For MySQL/MariaDB, install `mariadb-dump`/`mariadb` or
`mysqldump`/`mysql`. Use a dedicated application database and transactional tables;
the account needs dump and restore privileges. Unsupported URL connection
options are rejected before downtime. Resolve any job still marked `running`
before updating; the updater will not guess whether an email was delivered.

Protected recovery records, command diagnostics and backups remain under
`/var/backups/copydesk` by default. They contain credentials and personal data.
Retain them according to your backup policy and replicate them to protected
off-host storage. Automatic updates do not prune recovery data. Preparation and
restoration need space for new code and multiple instance/database copies.

```sh
sudo quillsail update status
sudo quillsail update --resume
sudo quillsail update rollback --acknowledge-mail-replay
```

Failures before commit restore the old code and matching data automatically.
If power or the process is lost, `--resume` uses the durable journal to recover
or finish. Keep the maintenance marker and recovery files intact until recovery
completes. After commit, recovery never automatically overwrites new reader
content or replays old queued mail by restoring an earlier database.

Explicit `rollback` first saves the current instance and database, then restores
the previous update's matching snapshot and application. New content received
since that snapshot is absent from the active publication after rollback and
remains in the safety backup. Review the snapshot and delivery history on an
isolated instance first: restored pending jobs can resend mail already delivered
after the snapshot. The command requires explicit acknowledgment of that risk.
Alembic downgrade is not used.

This lifecycle requires a registered versioned systemd or Compose deployment.
An arbitrary editable checkout, shared database, custom process supervisor or
multi-host deployment needs its own orchestration; the updater refuses to guess
how to stop or recover it.
