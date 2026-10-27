# Changelog

## 1.7.0 — Beta

- Add an optional desktop launcher with automatic local server startup, browser setup, a background worker, start/stop controls, logs and a persistent publication folder. The CLI and server installations remain available.
- Authorize first-run browser setup using a short-lived, single-use local launch ticket. Preserve CSRF and normal account sign-in; isolate desktop configuration from server environment variables and block cross-origin/network access.
- Back up desktop data before schema upgrades. Add separate native builds for Apple Silicon Macs, Intel Macs, Windows x64 and Linux x64, with browser and GUI lifecycle checks. An explicit maintainer workflow can publish all four verified downloads together with checksums; ordinary builds do not publish a release.

## 1.6.0 — Beta

- Customize the homepage hero independently of the publication name: title text, a logo, or both, plus an optional subtitle and overline. Upload dedicated light/dark hero logos or reuse the publication logo, and adjust the logo width.
- Preview homepage changes privately before saving. Hero settings carry across design imports and remain in place when loading a style preset.

## 1.5.2 — Beta

- Add a `quillsail` command. Help text, documentation, the systemd worker unit and the container now use it. `copydesk` keeps working as an alias, so existing scripts, cron jobs and services need no changes.

## 1.5.1 — Beta

- Embed uploaded publication logos as related PNG image parts in outgoing email, so their display does not depend on an external image download. Preserve browser preview URLs and plain-text alternatives across SMTP, Gmail and Outlook.
- Build email logo URLs from the configured public origin when rendering behind a reverse proxy.

## 1.5.0 — Beta

- Add a publication design studio with Classic, Broadsheet, Brief, Reference and Canvas starting styles; fine controls for typography, color, masthead, reading width, spacing, story layout and email presentation.
- Upload publication, dark-background and email logos plus a publication icon. Preserve transparent palette PNGs when processing uploads.
- Preview unsaved website and newsletter designs on desktop and mobile without changing live settings, creating uploads or sending mail. Keep theme previews isolated from concurrent readers.
- Export and import portable design settings. Support custom website CSS, offline email CSS inlining and existing template theme overrides.
- Keep publication styles out of the administration interface and retain existing styling until an administrator saves a design.

## 1.4.2 — Beta

- Place the QuillSail logo after the full “Powered by QuillSail” credit in web and email footers.

## 1.4.1 — Beta

- Keep post-login return URLs inside the configured installation path, including already signed-in visits and links to nested dashboard pages. Preserve query strings and fragments, avoid duplicate prefixes, and reject unsafe return paths.
- Integrate the supplied purple QuillSail logo into setup, dashboard branding and icons, public credits, email footers, and the repository readme. Use a white rendering on dark web surfaces while preserving publication branding.

## 1.4.0 — Beta

- Rename product branding to **QuillSail** across the interface, email copy, command output, and documentation. Existing `copydesk` commands, configuration names, and integration identifiers remain compatible.
- Share expiring, revocable private draft previews without accounts. Links stay out of archives and feeds; restricted articles and group-only blocks are excluded.
- Offer an opt-in business outreach form with email confirmation, a private staff inbox, follow-up notes, and reviewed opportunity-to-article handoff.

## 1.3.0 — Beta

- Choose Newsletter, Blog, or Blog + newsletter during setup or in General settings.
- Publish blog posts directly with publisher permission, and reuse posts in draft newsletter issues.
- Offer public blog posts to another QuillSail publication through its editorial workflow, retaining source credit.

## 1.2.0 — Beta

- Host competitions with moderated text/project entries, assigned judges, reader voting, deadlines and published results.

## 1.1.0 — Beta

- Connect Gmail or Outlook through OAuth after one-time app registration.
- Manage encrypted SMTP settings or import a configuration file from the dashboard.

## 1.0.0 — Beta

- Initial newsletter CMS with editorial review, reader groups, subscriptions, issue composition, themes, extensions and background email delivery.

See [verification](docs/verification.md) for the tested scope and remaining limits. Deployment and release-automation files are being prepared for the initial public repository; no public package or container registry publication is implied by these entries.
