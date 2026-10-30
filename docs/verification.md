# Verification record

QuillSail is beta software. These are development checks, not an independent security audit. Private deployment addresses, access details and operations records are excluded from the public repository.

## Application validation

| Version | SQLite | PostgreSQL | Scope |
| --- | --- | --- | --- |
| 1.11.2 | Application suite passed locally | PostgreSQL and MariaDB passed PR CI | Mac Developer ID signing and notarization: a manual Desktop bundles run on the release branch signed every binary with the hardened runtime and a secure timestamp, and Apple notarized the app and DMG for Apple Silicon and Intel. Stapled tickets validated; the installed DMG app, updater replacement, and setup/restart and GUI checks passed under the hardened runtime with no entitlements. Signing unit tests (Mach-O detection, inside-out order, ticket-service retries) and the publish guard against un-notarized Mac builds passed. Website, Windows and Linux builds passed |
| 1.11.1 | Application suite passed locally | PostgreSQL and MariaDB passed PR CI | Graphical installers: native Apple Silicon DMG verified locally; all four native builds passed PR #15, including install, launch, archive replacement and removal. Website, Python 3.11–3.14, packaging and container CI passed. Release-version checks and publication are tracked in #16 |
| 1.11.0 | 431 passed, 3 skipped | Not rerun locally | Full browser transfer across separate local publications, sign-in restoration, creator blurbs, three per-issue text choices, attribution, CSRF and permissions, restricted-body protection, relative images/links and schema upgrade preservation. Six browser-script cases passed in Node; the handoff and composer were checked in Chrome, including a 390px layout. Website: 115 passed, 1 skipped. Ruff, generated references, wheel/sdist metadata, release boundaries and clean wheel installation passed. Database, Docker and native desktop builds are checked by GitHub Actions |
| 1.10.3 | 421 passed, 3 skipped | Not rerun locally | Public website hero layouts, canonical metadata, structured data, breadcrumbs, indexing rules and mobile download overflow. Website: 115 passed, 1 skipped; public crawl checked 43 canonical pages. CI workflows passed actionlint; package metadata, clean wheel installation and server-update recovery checks passed locally. Database, container and native platform checks remain covered by GitHub Actions |
| 1.10.2 | 421 passed, 3 skipped | Not rerun locally | Deleting Default publication, stopping owned runtimes and workers before deletion, cancellation and stop-failure preservation, persistent empty instance lists, and creating, registering, refreshing or checking for updates from an empty launcher. Real launcher checked at narrow width with disposable data. Website checks: 82 passed, 1 skipped |
| 1.10.1 | 409 passed, 3 skipped | Not rerun locally | Desktop and CLI deletion of stopped named instances, explicit permanent file deletion, confirmation, running-instance locks, missing or replaced folder checks, and launcher controls. Website checks: 82 passed, 1 skipped; contributor design proposals are excluded from published documentation |
| 1.10.0 | 401 passed, 3 skipped | Not rerun locally | Desktop launcher restyle with light, dark and automatic appearance, readable dialogs, the bundled Amiri Bold heading font, first-time setup that follows the launcher's appearance, background running after the window closes, and quitting from the button, menus, tray and `quillsail quit`. Launcher and setup rendered on macOS (Qt and headless Chrome, including dark mode, form errors and a 390px viewport); close-to-background and quit checked on the native macOS platform |
| 1.9.1 (unreleased) | 373 passed, 3 skipped | Not rerun locally | Stable Linux server command installation, sudo PATH checks, conflict protection, bootstrap ordering, registration rollback, and command continuity across real disposable updates and recovery |
| 1.9.0 | 357 passed, 3 skipped | Not rerun locally | Managed server updates: release preparation, maintenance gate, instance/database snapshots, migration, web/worker validation, failure recovery, resume, manual rollback and retry, privileged-release and working-directory protections. Local server update smoke test passed on macOS; Compose updates and PostgreSQL/MariaDB backup restoration run in CI |
| 1.5.0 | 234 passed, 2 skipped | Not rerun | Publication design presets, permission/CSRF checks, isolated unsaved previews, logo variants/transparency/aspect ratio, design import/export boundaries, offline CSS inlining and existing workflows |
| 1.3.0 | 153 passed, 2 skipped | 154 passed, 1 skipped | Blog/newsletter modes, direct publishing permissions, same-site post reuse, attributed external submissions, no remote source fetching, editorial review, subscriptions, competitions, backup/restore |
| 1.2.0 | 134 passed, 2 skipped | 135 passed, 1 skipped | Competition hosting, moderation, assigned judges, concurrent reader voting, deadlines, ties and immutable results |
| 1.1.0 | 122 passed, 2 skipped | 123 passed, 1 skipped | Encrypted sender settings, OAuth state/PKCE, mocked provider APIs, refresh and switching |

The SQLite-only skip needs a disposable server database. The shared SMTP skip is an inapplicable TLS/authentication test on the plain loopback transport. A prior 1.0.0 validation also exercised MariaDB, Python 3.11–3.14, actual SMTP/TLS, and database backup/restore. Newer behavior was not independently rerun against MariaDB or every Python runtime; CI is configured to cover those combinations. The 1.5.0 check used Python 3.13 and SQLite, plus a clean wheel installation and browser checks of the editor, website/email previews and a narrow viewport. Received-email appearance in actual Gmail, Outlook and other clients still needs per-installation testing; browser email previews cannot establish client compatibility.

A private Linux reference installation was checked behind a reverse proxy with Gunicorn and a separate worker. Checks covered HTTPS/subpath routing, assets, secure cookies, CSRF, private drafts and revision history, issue composition/previews, competition draft isolation, host settings, and migration upgrades. Existing SMTP authenticated and a previously requested test message was confirmed in its recipient's inbox. These observations do not establish deliverability for other installations.

The Google authorization flow reached the provider's real sign-in page after app configuration. Account consent, token exchange and actual Gmail delivery remain unverified. Microsoft app registration is deferred. Automated Gmail/Outlook send and refresh tests mock provider HTTP responses; they are not live provider-delivery tests.

[Machine-readable application results](verification-results.json) retain test counts and sanitized deployment checks.

## Reproduce

```sh
python -m pip install -e '.[dev,integration]'
pytest
ruff check src tests scripts deploy
python -m build
python -m twine check --strict dist/*
python scripts/check_public_files.py
python scripts/check_public_files.py --archive dist/*.whl
python scripts/check_public_files.py --archive dist/*.tar.gz
python scripts/package_smoke.py dist/*.whl
python scripts/server_update_smoke.py dist/*.whl
```

The server update smoke test installs two synthetic versions of the built wheel into disposable virtual environments, runs real Gunicorn and worker processes, and exercises an update, a manual rollback and recovery from a failed health check. It replaces only systemd and trusts the interpreter that runs it, because development and CI interpreters live in shared tool caches that a production updater refuses.

The default suite uses disposable SQLite databases and synthetic accounts. Real loopback SMTP/TLS tests need the integration dependencies. To use a database server, also install the relevant driver and set `COPYDESK_TEST_DATABASE_URL` with a role permitted to create and drop test databases. Fixtures create randomly named `copydesk_test_*` databases and remove only those databases. Use a dedicated test server.

```sh
pip install -e '.[dev,integration,postgres]'
COPYDESK_TEST_DATABASE_URL='postgresql+psycopg://test_user:TEST_PASSWORD@127.0.0.1:5432/postgres' pytest
```

With Docker available, build an isolated image and run the container checks:

```sh
docker build -t copydesk:ci .
bash scripts/container_smoke.sh
```

The smoke script uses a separate Compose project and temporary volume, checks startup, web/worker health, non-root execution, path prefixes and persistence, then removes its own test resources. Never point it at an existing publication.

## Public repository preparation

Local preparation on October 26, 2026 passed **156 tests, 2 skipped** on Python 3.13/SQLite, including first-run bootstrap, private configuration permissions, stable secrets and refusal to start with an unmigrated database. The container entrypoint was additionally exercised outside Docker with real Gunicorn and worker processes, HTTP subpath routing, browser administrator setup, blog rendering and both health checks. A clean virtual environment installed the built wheel and passed migrations, setup, static-asset and subpath checks. Wheel/sdist metadata and public-file boundary checks passed. Staged files were also compared against known local deployment credentials and provider registration values without exposing those values.

The repository includes Python/database CI, package validation and a Docker smoke job. Release automation validates the version tag, attaches the wheel/sdist and publishes a versioned GitHub Container Registry image when a maintainer publishes a GitHub release. These workflows have not been dispatched to GitHub as part of local preparation. Docker is not installed in the preparation environment, so an actual image build/container run remains to be verified by CI or a Docker-equipped host.

## Limits

Long-term operations, a full capacity benchmark, Oracle MySQL, other database/runtime combinations, externally delivered webhooks, and provider-specific account policies remain outside the verified scope. Restore and inbox checks should be repeated for each deployment. No independent security audit has been performed.
