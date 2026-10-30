# Design: connect a domain and launch

**Status: proposal, not yet built.** This describes planned work for contributors and maintainers. Nothing here is available in a release yet. User documentation will go in `docs/hosting.md` once Phase 1 ships.

## Why

Today a desktop publication runs only on `127.0.0.1`, and putting it online means following the [server installation](../installation.md) guide. We want "three steps to go live": set up your publication, connect your domain, launch.

Admins can connect a domain from the dashboard. Until they're ready, readers see a launching-soon page that collects sign-ups. Connecting is always optional. People can keep working on their own computer, or use a host other than Cloudflare.

As far as we could find, no self-hosted newsletter or blog tool does this. Ghost, listmonk, Keila and Publii all leave the domain to the user. Self-hosting platforms such as Cloudron, YunoHost and Coolify have DNS or tunnel connectors, and we take lessons from them below.

## Goals

- Connect a domain on Cloudflare from the dashboard. QuillSail creates the tunnel and the DNS records.
- A launching-soon mode that works with any host, with a sign-up form.
- A manual "here are the records" screen with live checks, for every other DNS provider.
- Email DNS (SPF, DKIM and DMARC) set up safely, without breaking records the domain already has.
- Keep subscribe and unsubscribe links working while a desktop publication's computer is asleep.

## Not goals

- Supporting many registrars' APIs. YunoHost's list shrank to the few registrars people actually tested, and even some of those broke. We start with Cloudflare plus the manual screen and add providers only when users ask.
- Hosting QuillSail on Vercel or GitHub Pages. Both serve only static files, or functions without a permanent disk. Subscribing, unsubscribing, submissions and sign-in need a running QuillSail.
- Changing existing installs. Without an admin's choice, nothing connects and nothing is hidden.

## Rules for every phase

- **Existing installs don't change.** A missing launch state means `live`.
- **The core works with any host; providers plug in.** `domains.py` holds the record model and DNS checks. `cloudflare.py` is one provider plugged into it.
- **DNS safety.** QuillSail never replaces or deletes a record it didn't create. The only change it makes to an existing record is the SPF merge, and it saves the old value so the change can be undone. After every write, QuillSail checks the result directly with the domain's own nameservers.
- **Credentials** are encrypted like mail credentials ([email_connections.py](../../src/copydesk/email_connections.py), HKDF and Fernet), with their own `info` label. They never appear in logs or on a command line.
- **Writes and checks run as jobs** (`jobs.enqueue`), and every step can be retried safely. Only the reads needed to show a choice, such as the list of domains, run inside the request, with a short timeout.
- **Project conventions apply:** `audit.record(...)` for admin actions, new events added to `hooks.EVENTS`, plain-English copy, the form macros, no inline styles or scripts (strict CSP), and pages that work without JavaScript.
- **Tests** mock every Cloudflare call with `responses` and use a fake `cloudflared` script. Tests never touch the network.
- **The schema change needs the next available migration number** (`0006` already stores newsletter blurbs). It adds `HostingConnection` (encrypted provider credentials and state) and `ManagedRecord` (each DNS record QuillSail created or changed, with the provider's record ID and the previous value).

## Phase 0: Things to confirm first

1. **Cloudflare OAuth.** PKCE without a client secret is supported, which fits open source. Still to confirm: whether loopback redirect addresses with any port are allowed, the exact scope names, and the domain-verification process for a public app.
2. **Token permissions.** Find the permission keys for Zone Read, DNS Edit and Cloudflare Tunnel Edit with `GET /user/tokens/permission_groups`. The pre-filled token link needs them.
3. **Two hostnames in one app.** Check how Flask 3.1 routes requests for two hosts when `SERVER_NAME` is set. [app.py](../../src/copydesk/app.py) sets it from `base_url`, and [desktop.py](../../src/copydesk/desktop.py) pins it to the loopback address.
4. **Workers and KV free-plan limits**, and the "fail open" setting on Worker routes.
5. **How to ship `cloudflared`** (see the open decisions).

## Phase 1: Public address and launching-soon mode

### 1a. Public address

- **Servers:** `base_url` from the config file or environment stays the source of truth. A new Domain page shows it read-only, with the command to change it.
- **Desktop:** `site.base_url` in the instance's `copydesk.toml` becomes the public address once one is set.
  - `DesktopRuntime` keeps the loopback address in `self.url` and stops overwriting `config.base_url` when a public address exists.
  - A new `set_public_url()` writes the config with `write_private` and restarts the instance's listeners.
- **Session cookies:** `SESSION_COOKIE_SECURE` follows `base_url` today. That would break the plain-HTTP local dashboard in some browsers once the public address uses HTTPS. A small session interface will decide Secure for each listener instead.
- **Emails, feeds and the API** already build links from `config.origin` through `external_url()`, so they switch to the public address automatically.

### 1b. A public listener on desktop

When a public address is set, `DesktopRuntime.start` opens a second loopback server. It is wrapped in a new `PublicRequests`, the counterpart to `LocalRequests`:

- It accepts only requests that come from this computer (the tunnel runs locally) and are addressed to the public hostname.
- It marks requests as HTTPS and tags them `quillsail.listener = "public"`.
- It takes the visitor's address from the provider's header: `CF-Connecting-IP` for Cloudflare, or the last `X-Forwarded-For` hop for another host. **This step is required.** Rate limits are keyed on `request.remote_addr` (`security.client_ip()`), so without it every internet visitor would share one limit as 127.0.0.1.
- It returns 404 for `/_desktop/*`, `/setup/`, the dashboard, the API and the sign-in pages, because desktop editors work locally. Letting other editors sign in from the public address can be a later option, off by default.
- Its port is remembered in `desktop.json`, the same way the private port is.

The private listener and `LocalRequests` stay as they are.

### 1c. Launching-soon mode

- **New `launch.py` module.** The state (`soon` or `live`) is stored with `settings.set_value("launch.state", …)`. It's not declared with `define()`, so it stays out of the generic settings form. Missing means `live`.
- **Page content** is declared with `define()`: heading, message (Markdown), whether to show the sign-up form, and an optional launch date.
- **The gate goes in `_before`** in [app.py](../../src/copydesk/app.py), right after the maintenance check, and works the same way: an allowlist of endpoint names rather than paths, so it also works when the site lives under a path prefix. While the site is launching soon, visitors who can't use the dashboard reach only:
  - `public.home` (shows the launching-soon page), `public.health`, `public.robots`, `public.upload`, `public.theme_asset` and `static`
  - `subscribe.subscribe`, `subscribe.confirm`, `subscribe.manage` and `subscribe.unsubscribe`
  - on servers, the sign-in and password-reset pages

  Everything else redirects home. Feeds and the sitemap return 404, so titles don't leak.
- **Editors** see the full site, with a banner saying "Readers see the launching-soon page."
- **The page** is `templates/public/launching.html`. It extends the public layout, so both bundled themes style it and themes can override it.
- **The sign-up form** appears only when a real sender works. With the file backend, confirmation emails would never arrive, so the form is hidden and the dashboard says why.
- **Launch page** in the dashboard (`views/dashboard/launch.py`):
  - A checklist: the public address answers over HTTPS, the sender works, and DNS checks pass (once Phase 3 exists).
  - **Go live** and **Back to launching soon** buttons. Both are audited, and both fire a new `publication.launched` event, so the webhooks extension can announce the launch.
- **Setup** adds the question "Where will readers find your publication?" with three equal choices:
  - Connect Cloudflare
  - Use another host
  - Not now, keep it on this computer

  If they pick a domain, a second question follows: "Show a launching-soon page until you're ready?" It's on by default.

### 1d. The manual records screen

- **`domains.py`** defines a `RecordSpec` (name, type, value, purpose) and checks it with dnspython, a new dependency. Checks go directly to the domain's own nameservers, so a stale cache can't give a false answer.
- **A reachability check that works with any host:** fetch `{base_url}/healthz` and confirm it reports this install's ID (a new random `system.instance_id`). That proves the domain leads here, whatever sits in front.
- **The Domain page** lists the records with copy buttons, shows Found, Missing or Different for each, and has a **Check again** button.

### Phase 1 tests

- Each allowed and blocked endpoint, and existing installs staying live after an upgrade.
- Editors see the full site; it works under a path prefix.
- `PublicRequests` rejects a wrong host, a remote peer and blocked paths.
- The visitor-address header is trusted only on the public listener, and the Secure cookie flag is set per listener.
- The setup choices, and launch actions being audited and firing hooks.
- The sign-up form is hidden with the file backend.

## Phase 2: Cloudflare connector

### 2a. Credentials

- **API token first.** It needs no infrastructure from Dooba. **Create a token** opens Cloudflare's token page through its template link, with the permissions pre-filled. The user pastes the token back, and QuillSail verifies it, encrypts it, and stores it in `HostingConnection`.
- **OAuth second.**
  - Desktop uses loopback redirects, if Phase 0 confirms Cloudflare allows them.
  - Each server has its own domain, so servers need a redirect relay run by Dooba that passes the code and state back to the right install. Before redirecting, the relay must **show the destination address and require a click**. Otherwise someone could start a flow themselves and trick a victim into approving it, and the code would land on the attacker's install.

### 2b. Picking a domain

- Only active domains with full Cloudflare setup are listed. For domains still pending, the page shows the nameserver instructions and waits. With none, it links to Cloudflare Registrar and "Add a site." CNAME-only setup needs Cloudflare's Business plan, so we don't support it.
- The subdomain field defaults to `news`. **If a record already exists at that name, QuillSail stops and asks** the user to choose another name or remove the record themselves.

### 2c. Setup (`domain.provision` job)

The job resumes from the last finished step.

1. Find or create a tunnel named `quillsail-<instance id>`.
2. Set its routing: the hostname goes to the public listener (desktop) or the app port (server), with a final catch-all that returns 404.
3. Create a proxied CNAME to `<tunnel id>.cfargotunnel.com` with the comment "Managed by QuillSail", and save its ID in `ManagedRecord`.
4. Fetch the tunnel token and store it encrypted.
5. Run the `domain.verify` job: the domain's nameservers show the CNAME, then `/healthz` answers over HTTPS with this install's ID. It retries with backoff.
6. On desktop, call `set_public_url()`. On a server, if `base_url` doesn't match the hostname, show the command to change it.

### 2d. Running `cloudflared` (new `tunnel.py`)

- **The binary:** a pinned version and SHA-256 per platform. It's verified before it's ever made executable, and the pin is updated with each QuillSail release. `cloudflared` is Apache-2.0 licensed.
- **Starting it:** `cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:<port> run`, with the token passed in the `TUNNEL_TOKEN` environment variable, never on the command line, where other users could see it.
  - Its status comes from the `/ready` metrics endpoint.
  - It restarts with backoff and stops in `DesktopRuntime.stop()`.
  - There's one process per instance, covered by the existing instance lock.
- **Servers:** a `quillsail tunnel run` command plus a systemd unit in `deploy/systemd/`.

### 2e. Launcher

Each instance shows "Public address: news.example.com" with Online, Connecting or Offline, plus **Open public site**. The connect flow itself stays in the dashboard, so desktop and servers share it.

### 2f. Disconnecting

- QuillSail deletes only the records listed in `ManagedRecord`, deletes the tunnel, erases the credentials, and switches desktop back to its local address. Each step is audited.
- If Cloudflare returns 401 (access revoked), the Domain page and launcher show it.

### Phase 2 tests

- Every API step, including resuming after a failure.
- Stopping when a record already exists; pending domains; 401 handling.
- The fake binary's `/ready` endpoint; restarting and stopping.
- The token never appears in command lines or logs.
- Disconnecting deletes only QuillSail's own records.

## Phase 3: Email DNS records

- **Find the sending domain** from `effective_mail_config()`.
  - Gmail.com, outlook.com and similar addresses: there's nothing to do, and the page says so.
  - A sending domain outside the connected Cloudflare domain gets the manual list from 1d.
- **Provider presets** in `domains.py` (Google Workspace, Microsoft 365, Amazon SES, Postmark, Mailgun, SendGrid, Resend) give each provider's SPF include and say where its DKIM records come from.
- **DKIM:** the provider holds the keys, so the user pastes the records from the provider. A parser accepts lines in "name type value" form. DKIM records are created only at names that are empty. A different existing value is shown as a conflict.
- **SPF:** a domain can have only one SPF record. Adding a second one breaks email authentication for the whole domain, including mail it already sends. `merge_spf()` follows these rules:
  - No SPF record: create `v=spf1 include:X ~all`.
  - One record: add the include just before the `all` term and leave everything else exactly as it is, including their `all` setting.
  - Two or more records (already broken), or a `redirect=`: don't touch anything; explain the problem and show the fix.
  - Count DNS lookups through every include, and refuse if the total would exceed SPF's limit of 10.
  - Save the old value in `ManagedRecord` so **Undo** can restore it exactly.
- **DMARC:** if no `_dmarc` record exists, create `v=DMARC1; p=none;` (monitoring only) and explain what that means. If one exists, leave it alone and show its policy.
- **Statuses** appear on the Email settings page and in the Launch checklist. `quillsail check` reports SPF, DKIM and DMARC.

### Phase 3 tests

Table-driven SPF cases: none; `-all`; include already present; multiple records; `redirect=`; over the lookup limit; values over 255 characters. Also DKIM and DMARC conflicts, and Undo restoring the exact old value.

## Phase 4: Keeping the site up while the computer sleeps

This phase is for Cloudflare only and is opt-in. A desktop publication is offline whenever its computer is. Readers missing pages for a while is tolerable. Unsubscribe links failing is not: CAN-SPAM requires them to keep working for 30 days after a send, and Gmail and Yahoo enforce one-click unsubscribe.

- **Extra permissions** (Workers Scripts, Workers Routes, KV) are requested only when this is turned on.
- **The Worker** is a plain JavaScript module shipped as package data (`src/copydesk/edge/fallback.js`, no build step). It's deployed with a KV binding on the route `host/*`, set to **fail open**: past the free plan's daily limit, traffic goes straight to QuillSail instead of failing.
- **While the computer is reachable**, the Worker passes everything through. **When it isn't** (Cloudflare returns 530 or 52x):
  - `GET /` serves the saved snapshot.
  - Subscribe and unsubscribe POSTs, including RFC 8058 one-click unsubscribes, are queued in KV with the time received.
  - Everything else gets a friendly offline page.
  - The Worker enforces size caps and per-visitor limits.
- **The wording stays accurate:** "You're unsubscribed." "Your confirmation email will arrive shortly."
- **Snapshots:** an `edge.snapshot` job pushes the launching-soon page, with its CSS inlined, whenever it changes. Home and recent issues can come later.
- **Processing the queue:** once the tunnel is back up, a maintenance task reads the KV queue through the API and replays each entry through the normal subscriber functions.
  - Tokens are checked at that point; invalid ones are dropped.
  - Unsubscribes take effect as of the time they were received.
  - Keys are deleted after the commit, and replaying an entry twice is harmless.
  - The result is audited.
- **Warnings and extras**
  - A warning appears if the site has been offline for more than 3 days with unsubscribes waiting, since CAN-SPAM allows 10 business days.
  - An optional "Keep this computer awake while your publication is public" setting (`caffeinate` on macOS, `SetThreadExecutionState` on Windows, `systemd-inhibit` on Linux).

## Phase 5: Domain Connect

Build this only when users ask for a specific registrar. [Domain Connect](https://datatracker.ietf.org/doc/html/draft-ietf-dconn-domainconnect-03) is an open standard, now going through the IETF, supported by GoDaddy, IONOS and Cloudflare among others. It only writes DNS records and can't make a computer reachable, so it fits server installs and email records best.

- **Templates:** Dooba submits two templates to the Domain Connect templates repository:
  - `quillsail.web`: an A or CNAME record.
  - `quillsail.email`: uses the standard's own SPFM record type, which merges into an existing SPF record, plus DKIM and DMARC.
- **Flow:** find the provider through the `_domainconnect` TXT record, send the user through the synchronous flow, then check the result with the same checker from 1d.
- **Signing:** a signing key can't live inside open-source installs. Either use unsigned templates, where providers show a warning, or have Dooba run a signing relay. Decide when we get here.

## Docs and releases

**Docs**

- A new `docs/hosting.md` covering connecting a domain, launching soon, manual records, Cloudflare, the offline fallback and disconnecting.
- Updates to `desktop.md` (replace "Local URLs work only on your computer"), `installation.md`, `configuration.md` and `extensions.md` (the new events).
- CHANGELOG entries, results in `verification.md`, and CLAUDE.md's file table and next migration number.

**Release order**

| Release | Contents |
| --- | --- |
| 1.11 | Phase 1: launching soon, public listener, manual records |
| 1.12 | Phase 2: Cloudflare connector |
| 1.13 | Phase 3: email DNS |
| 1.14 | Phase 4: offline fallback |
| Later | Phase 5: Domain Connect, on demand |

## Open decisions

1. **OAuth relay for servers:** is Dooba willing to run a small relay service? If not, servers use API tokens only and OAuth stays desktop-only.
2. **`cloudflared`:** download it with the pinned hash on first connect, which keeps bundles small (recommended), or bundle it in all four desktop builds.
3. **Testing the Worker:** the repo has no Node today. Either CI adds it to run the Worker locally, or we keep the Worker very small and check it by hand against a test domain.
4. **Default for new installs that connect a domain:** start in launching-soon mode (recommended).

## References

- [Cloudflare: create an OAuth client (PKCE)](https://developers.cloudflare.com/fundamentals/oauth/create-an-oauth-client/)
- [Cloudflare: API token template links](https://developers.cloudflare.com/fundamentals/api/how-to/account-owned-token-template/)
- [Cloudflare: create a remote tunnel with the API](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/)
- [Cloudron domains](https://docs.cloudron.io/domains/), [YunoHost registrar API](https://doc.yunohost.org/admin/get_started/providers/registrar/), [Coolify Cloudflare Tunnels](https://coolify.io/docs/integrations/cloudflare/tunnels/server-ssh)
