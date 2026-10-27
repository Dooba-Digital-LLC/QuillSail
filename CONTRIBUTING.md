# Contributing to QuillSail

Install Python 3.11+, create a virtual environment, and run `pip install -e '.[dev]'`.
Run `pytest` and `ruff check src tests scripts deploy` before proposing changes. Include regression tests for changes to permissions, tokens, editorial transitions, subscriptions, or job delivery. Never use a live subscriber list in tests.

The Flask application factory is in `src/copydesk/app.py`. Business rules live in `workflow.py`, `groups.py`, `subscribers.py`, and `newsletter.py`; views handle requests and validation. Templates and static assets ship in the Python package. Database changes need an Alembic revision under `src/copydesk/migrations/versions/` and an upgrade test. Preserve SQLite compatibility and verify other database dialects before claiming support for changes that affect SQL.

Use plain, clear interface copy. Check narrow screens, keyboard navigation, form errors, and both public and dashboard layouts. Test HTML and plain-text versions when changing emails. Avoid adding publication-specific branding to core behavior; use settings, themes, or extensions.

Describe the problem, changed behavior, and validation in a contribution. Contributions are offered under the repository's MIT license. Discuss substantial architectural changes with maintainers before implementing them.

For package changes, run `python -m build`, `python -m twine check --strict dist/*`, the release-boundary checks, and `python scripts/package_smoke.py dist/*.whl`. With Docker available, build `copydesk:ci` and run `bash scripts/container_smoke.sh`. CI mirrors these checks. Keep application configuration, credentials, instance data and private operations notes out of Git and build contexts.
