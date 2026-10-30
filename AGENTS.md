# Working on QuillSail

QuillSail is a generic, MIT-licensed blog and newsletter CMS. Keep organization-specific branding and deployment details in instance settings, themes, or private operations records.

- Write the product name as **QuillSail**, with capital Q and S. The command is `quillsail`; `copydesk` remains a working alias. The `copydesk` package, configuration, and integration identifiers remain stable for compatibility.
- Core code is under `src/copydesk/`. Views validate requests; domain modules own business rules.
- Preserve SQLite, PostgreSQL and MySQL/MariaDB portability. Schema changes need an Alembic migration and upgrade coverage.
- Keep drafts, restricted content, judge notes, subscriber tokens and credentials private. Every state-changing form needs CSRF protection. Permissions must be checked on the server.
- Use existing Jinja form/UI macros, plain interface language, and progressive enhancement. Check narrow layouts and accessible labels.
- Use the background queue for email. Tests use synthetic users, disposable databases and local/mocked mail; never send real email or publish test content on a live installation.
- Never commit environment files, provider credentials, instance data, exports, backups, or `.validation/`.
- Run `pytest` and `ruff check src tests scripts deploy` for code changes. Validate wheel/sdist contents when changing packaging. Report any checks that could not be run.
- Read `CONTRIBUTING.md` and the relevant documents under `docs/`. Do not publish a package, container or GitHub release unless the user requests publication.
- `website/` is the separate Flask app for quillsail.blog and the docs site (see `website/README.md`). It renders `docs/`; list new docs pages in `website/docs.toml`, and run `cd website && pytest && ruff check .` after changing either. After changing commands, settings, permissions, workflow transitions or hooks, run `python scripts/build_reference_docs.py` to regenerate `docs/reference/`.
