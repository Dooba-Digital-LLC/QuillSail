# QuillSail

<img src="assets/QuillSail.svg" alt="QuillSail quill logo" width="120" height="101">

Self-hosted blogs and newsletters where readers write.

Built by [Dooba Digital](https://github.com/Dooba-Digital-LLC).

QuillSail brings blog publishing, reader submissions, editorial review, customizable community groups, and newsletter delivery into one application. Organizations host their own installation and own their data. Choose Newsletter, Blog, or Blog + newsletter. The publication name, branding, groups, permissions, pages, and email content are configurable.

The product name is **QuillSail**, with capital Q and S. The command is `quillsail`. The older `copydesk` command still works as an alias, and the `copydesk` Python package, configuration names, and integration identifiers are unchanged for compatibility with installed sites.

The project originated in work for **The Tech Stack**, a publication operated by the University of South Carolina Association for Computing Machinery. Other installations choose their own name, branding and community rules.

## What it includes

- A blog mode with direct publishing for trusted publishers, plus optional inclusion in local or separately hosted newsletters.
- Markdown writing with previews, image uploads, co-authors, revision history, and submission timelines.
- Editorial review, feedback, change requests, acceptance, publication, and email notifications.
- Custom groups such as students, staff, and alumni, with custom profile fields, approval rules, domain rules, badges, and permissions.
- Double opt-in subscriptions without requiring an account; preference management and one-click unsubscribe.
- An issue composer with articles, headings, text blocks, audience targeting, test emails, scheduling, and delivery reports.
- Competitions with moderated text/project entries, appointed judges, reader voting, deadlines, and published results.
- Business outreach with confirmed contact emails, a private dashboard inbox, follow-up notes, and opportunities that can become editorial drafts.
- Private draft issue links for external feedback, with expiry, revocation, and no public listing.
- A publication designer with logo variants, five original starting styles, typography, color and layout controls, private website/email previews, custom CSS, and portable design files.
- Public archives, search, Atom feeds, informational pages, and account data export/deletion.
- Themes with template inheritance and Python extensions with events, filters, settings, commands, and background jobs.
- SQLite by default; optional PostgreSQL and MySQL drivers. SMTP delivery with a persistent job queue.

## Deploy with Docker Compose

From a checkout of this repository, with Docker and its Compose plugin installed:

```sh
cp deploy/compose.env.example deploy/compose.env
docker compose --env-file deploy/compose.env up -d --build
```

Open `http://localhost:8080`, then retrieve the first-run setup code:

```sh
docker compose --env-file deploy/compose.env exec web cat /var/lib/copydesk/data/setup-code.txt
```

Create your administrator and choose **Newsletter**, **Blog**, or **Blog + newsletter**. There are no default production credentials. Configuration and data persist in a named volume, with separate web and worker containers. Email initially stays in the local file backend until you connect a sender.

For a public deployment, configure your HTTPS hostname and reverse proxy using the [installation guide](docs/installation.md). The software is **beta**; keep backups and verify delivery on your installation.

## Try it with Python

Requires Python 3.11 or newer. From this source directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
quillsail init --dir ./instance --site-name 'My publication' --mode hybrid --base-url http://localhost:5055
quillsail --config ./instance/copydesk.toml serve --port 5055
```

Open http://localhost:5055 and sign in with the administrator account you created. The development server runs a background worker. Email defaults to the console backend, so messages appear in the server log. To add fictional example content on an empty test site:

```sh
quillsail --config ./instance/copydesk.toml demo
```

Demo accounts use `demo-password-123`; use the demo only in a local test installation.

## Documentation

- [Server installation](docs/installation.md)
- [GitHub setup and releases](docs/github.md)
- [Configuration](docs/configuration.md)
- [Email and delivery](docs/email.md)
- [Connect Gmail, Outlook, or an SMTP server](docs/email-connectors.md)
- [Editorial and administrator guide](docs/user-guide.md)
- [Business outreach and private issue previews](docs/business-outreach.md)
- [Themes](docs/theming.md)
- [Extensions](docs/extensions.md)
- [Public API](docs/api.md)
- [Follow-up review](docs/review.md)
- [Database, SMTP, and university deployment verification](docs/verification.md)
- [Backups and upgrades](docs/maintenance.md)
- [Contributing](CONTRIBUTING.md) and [security reporting](SECURITY.md)

## Development

```sh
pip install -e '.[dev]'
pytest
ruff check src tests scripts deploy
```

Tests use temporary SQLite databases by default. Install `.[integration]` for real loopback SMTP/TLS tests, or set `COPYDESK_TEST_DATABASE_URL` with the appropriate database driver to run against disposable PostgreSQL or MySQL/MariaDB databases. CI also checks Python 3.11–3.14, built distributions and container startup. See the [verification report](docs/verification.md) for actual runs and remaining limits. This project has not undergone an independent security audit.

Licensed under the [MIT License](LICENSE).
