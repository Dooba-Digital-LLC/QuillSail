# Contributing to QuillSail

Install Python 3.11+, create a virtual environment, and run `pip install -e '.[dev]'`.
Run `pytest` and `ruff check src tests scripts deploy` before proposing changes. Include regression tests for changes to permissions, tokens, editorial transitions, subscriptions, or job delivery. Never use a live subscriber list in tests.

The Flask application factory is in `src/copydesk/app.py`. Business rules live in `workflow.py`, `groups.py`, `subscribers.py`, and `newsletter.py`; views handle requests and validation. Templates and static assets ship in the Python package. Database changes need an Alembic revision under `src/copydesk/migrations/versions/` and an upgrade test. Preserve SQLite compatibility and verify other database dialects before claiming support for changes that affect SQL.

Use plain, clear interface copy. Check narrow screens, keyboard navigation, form errors, and both public and dashboard layouts. Test HTML and plain-text versions when changing emails. Avoid adding publication-specific branding to core behavior; use settings, themes, or extensions.

Describe the problem, changed behavior, and validation in a contribution. Contributions are offered under the repository's MIT license. Discuss substantial architectural changes with maintainers before implementing them.

## Issue, branch, pull request and release workflow

For requested implementation work, use this workflow without asking the requester to repeat each routine step. Replace `<contributor-branch>` with the contributor's working branch and `<issue-number>` with the tracked issue. Use the configured repository remote; a contributor branch does not imply a separate GitHub account or fork.

1. Create or update the GitHub issue before implementing. Record the expected behavior and acceptance checks.
2. Fetch `origin`, inspect the working tree and contributor branch, and implement on an isolated branch. Preserve unrelated work. Update the relevant guides, add migrations and upgrade coverage for schema changes, and run the required checks above. Regenerate reference docs when their sources change; documentation changes also need the website checks.
3. Commit and push the completed change to `<contributor-branch>`, then open a pull request against `main` with `Closes #<issue-number>`, a description of the behavior, and validation results.
4. Rebase the contributor branch onto the latest `origin/main`, resolve conflicts, and rerun affected checks. If the rebase rewrites an already-pushed branch, use `git push --force-with-lease`, never an unconditional force push. Check for remote changes first.
5. Wait for the pull request's required CI, including database, package, website and applicable native desktop checks. Fix failures, then merge using GitHub's **Rebase and merge** method and synchronize the local `main`. Preserve the contributor branch for subsequent work.
6. When a release is requested, choose the next appropriate unused version, update the version, changelog and verification record in the reviewed change, and publish from the merged commit. Follow [GitHub setup and releases](docs/github.md#release-a-version). For a desktop beta, dispatch **Desktop bundles** on `main` with **publish** enabled; verify all four downloads, the server package and the signed update feed. Report the issue, pull request, release and any unrun checks.

Issue creation, implementation, pushing, pull-request creation and routine rebase/merge are the default contribution path for requested code changes. Publishing a release or container still requires an explicit release request; do not publish as a side effect of an ordinary fix.

For package changes, run `python -m build`, `python -m twine check --strict dist/*`, the release-boundary checks, and `python scripts/package_smoke.py dist/*.whl`. With Docker available, build `copydesk:ci` and run `bash scripts/container_smoke.sh`. CI mirrors these checks. Keep application configuration, credentials, instance data and private operations notes out of Git and build contexts.
