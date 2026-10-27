# Maintenance

Always back up the configuration (especially its secret key), database, uploaded images, local themes, and extensions. Treat exports as sensitive: they contain personal data, password hashes, and tokens.

## Backup and restore

For a consistent application JSON export, stop web and worker processes first, then run:

```sh
copydesk --config /var/lib/copydesk/copydesk.toml export /secure/backup/copydesk.json
```

Copy the instance configuration, uploads directory, themes, and extensions to protected storage. For native database backups use the database's consistent backup facility. Do not simply copy a running SQLite database while ignoring its WAL files.

To restore, prepare an isolated empty database and configuration using the same QuillSail version and secret key. Do not run `init` on the restore target because it creates an administrator. Create the configured data directory (and any parent directory for a SQLite database file). Point the configuration at the empty database, then run:

```sh
copydesk --config /path/to/restored/copydesk.toml import /secure/backup/copydesk.json
```

Copy uploads, themes, and extensions to the configured locations. Verify sign-in, published content, subscriptions, and the queued-job list before starting the worker. Restoring a backup can restore pending jobs: inspect these before enabling SMTP to avoid sending old mail. Keep the original backup until recovery has been tested.

## Upgrade

Stop web and worker processes, take a backup, install the new package, run `copydesk upgrade` with the instance configuration, then restart services. Test on a restored staging instance first. Migrations run through Alembic; downgrades are not a supported operational recovery mechanism. Roll back by restoring the previous package and its matching database backup.

Check `/healthz` for database reachability, the dashboard System page for worker activity, and service logs for errors. Health checks do not prove SMTP or worker availability. `copydesk check`, `copydesk jobs list`, `copydesk user --help`, and `copydesk subscribers --help` provide maintenance commands. Keep dependency updates and restore rehearsals on the organization's maintenance schedule.
