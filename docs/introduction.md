# How QuillSail works

QuillSail runs one publication: a blog, a newsletter, or both. Its readers can become its writers. This page explains the main ideas and where to read more about each.

## A publication

Each QuillSail installation runs a single publication with its own name, design, pages, groups and subscribers. Choose what it is during setup, and change it later under **Settings → General**:

- **Newsletter**: issues are the main event. The homepage leads with the latest issue and a sign-up form.
- **Blog**: posts come first, in a chronological homepage with an RSS feed. Email subscriptions are hidden.
- **Blog + newsletter**: posts on the website, plus subscriptions and newsletter issues.

Switching keeps every post, issue, subscriber and setting. See [blog and newsletter modes](publication-modes.md).

To run more than one publication, run more than one installation. The desktop app and the `quillsail instance` commands make that easy on one computer; see [multiple publications](desktop.md#multiple-publications).

## People

QuillSail separates people who read by email from people who sign in:

- **Visitors** read whatever is public.
- **Subscribers** get the newsletter by email. They need only an email address, which they confirm by clicking a link (double opt-in). See [subscribers and accounts](subscribers.md).
- **Members** have an account with a confirmed email address. Members can write, comment on their own submissions, join groups and read members-only articles.

One person can be both a member and a subscriber. Their account settings show both.

## Groups and permissions

Groups do two jobs. They're roles, such as **Editors** and **Administrators**, and they're kinds of readers, such as **Students**, **Staff** and **Alumni**. Each group has its own permissions, and a person's permissions are everything their groups allow.

Reader groups decide how people join: openly, by asking for approval, or only when an administrator adds them. They can also admit people automatically by email domain, ask for profile fields such as a graduation year, and show a badge and byline. Issues can be aimed at reader groups.

People can grant only the permissions they already hold, so nobody can give themselves more power, and administrators always keep every permission. See [groups and permissions](groups-and-permissions.md).

## Writing and review

Writing moves through a clear set of statuses:

```text
draft → submitted → in review → accepted → published
                         ↘ changes requested ↗
```

Writers draft in Markdown and submit when ready. Drafts are private to their writers. Editors pick up submissions, leave feedback, request changes, accept, decline or publish. Every step is recorded on the piece's timeline, written to the activity log and emailed to the people involved. Trusted writers can be allowed to publish directly. See [writing and review](writing-and-review.md) and the full [editorial workflow](reference/workflow.md).

## Newsletter issues

An issue is an ordered list of items: articles, section headings and text blocks. Any item can be aimed at particular reader groups, so alumni and students each get the parts meant for them in the same send. Editors preview each version, send themselves a test, then send it now or schedule it. See [newsletter issues](newsletter-issues.md).

## Email and background work

QuillSail never sends email while someone waits for a page to load. Every message, from a password reset to a 5,000-person issue, is saved as a background job in the same database transaction as the change that caused it. A separate **worker** process sends the jobs, pacing newsletters to your mail server's limits. If the worker stops, mail waits safely until it starts again.

Mail goes out through an SMTP server, or through a Gmail or Outlook account that an administrator connects. QuillSail adds no open or click tracking. See [email and delivery](email.md).

## Where it runs

QuillSail is a Python web application with a database and a worker. You can run it three ways:

- **The desktop app** runs everything on your computer, including the worker. It's private to your computer: the quickest way to try QuillSail and prepare a publication.
- **Docker Compose** runs separate web, worker and setup containers on a server.
- **The Python package** runs under your own process manager, such as systemd, with Gunicorn behind a reverse proxy.

Servers can use SQLite, PostgreSQL or MySQL/MariaDB. See [ways to install](install.md).

## Extending QuillSail

- **Design**: the built-in designer covers type, color, layout and email presentation. Template themes can override any page or email. See [design and themes](theming.md).
- **Extensions**: Python modules that react to events, change values with filters, add HTML to template slots, and add settings, permissions, commands and pages. See [extensions](extensions.md) and the [hook reference](reference/hooks.md).
- **API**: published content is available as JSON, and an endpoint accepts sign-ups from other websites. See the [public API](api.md).

## Words used in these docs

Article
: A piece of writing. Blog modes call published articles *posts*.

Issue
: One edition of the newsletter, numbered in order.

Instance
: One installation of QuillSail: its configuration, database, uploads and queue.

Worker
: The background process that sends email and runs other jobs.

Dashboard
: The administration area at `/dashboard/`, shown to people whose groups grant them something to manage.
