# QuillSail website

The source of three sites, served by one small Flask application:

| Address | What it serves |
| --- | --- |
| `quillsail.blog` | The homepage, downloads, the terminal installers (`/install.sh`, `/install.ps1`) and the release API |
| `docs.quillsail.blog` | The documentation, rendered from this repository's `docs/` folder, with search |
| `updates.quillsail.blog` | A stable address for the signed update feed, which redirects to GitHub Releases |

The design follows QuillSail's interface style in [`assets/README.md`](../assets/README.md): ink on paper, hairline rules, Amiri Bold headings, and the logo purple as the only accent. Light is the default; visitors can choose Dark or Automatic, as in the desktop launcher. Pages work without JavaScript, and a strict Content Security Policy allows only the site's own files.

## Run it locally

From the repository root, with Python 3.11 or newer:

```sh
python -m venv website/.venv
website/.venv/bin/pip install -e 'website[dev,server]'
cd website && .venv/bin/python -m quillsail_site
```

Open <http://localhost:8000> for the site and <http://docs.localhost:8000> for the docs. Browsers send `*.localhost` to your own computer. Changes to Python files, templates, `docs/*.md` and `docs.toml` reload the server.

Release information comes from the live signed feed, so the first page load needs an internet connection. To work offline, point `QUILLSAIL_RELEASES_FIXTURE` at a JSON file like the one in `tests/conftest.py`.

## How downloads work

Release details aren't stored in the site. `releases.py` reads QuillSail's [signed update feed](../docs/desktop-updates.md) with the same TUF client and trust root as the desktop app (`quillsail_site/trust/root.json`, a copy of `src/copydesk/updates/root.json`; a test keeps them identical). It checks every signature, expiry date and version, applies the app updater's rules to each entry, and keeps the newest release for each channel in memory. It refreshes every ten minutes in the background, and if GitHub can't be reached it keeps serving the last verified result.

So the download buttons, `/download/PLATFORM`, the release API and the installers only ever offer archives whose size and SHA-256 checksum are signed. Publishing a release with the **Desktop bundles** workflow updates the site automatically; there's nothing to deploy.

| Endpoint | Returns |
| --- | --- |
| `/download/PLATFORM` | A redirect to the newest download: `macos-arm64`, `macos-x86_64`, `windows-x86_64`, `linux-x86_64` or `server` |
| `/api/v1/releases/latest` | JSON for every download, with versions, sizes, SHA-256 checksums and URLs |
| `/api/v1/releases/latest/PLATFORM` | JSON for one download |
| `/api/v1/releases/latest/PLATFORM.txt` | The same as `key value` lines, for shell scripts |
| `/install.sh`, `/install.ps1` | The installers, with this site's address filled in |
| `/healthz` | Status, including the last verified release |

Add `?channel=stable` to choose the stable channel; the default comes from `QUILLSAIL_CHANNEL`.

## The installers

[`install.sh`](quillsail_site/install/install.sh) (macOS and Linux, bash 3.2 or newer) and [`install.ps1`](quillsail_site/install/install.ps1) (Windows PowerShell 5.1 and PowerShell 7) install the desktop app for the current user. They read release details from this site, check each download's size and checksum, refuse archives with paths outside the app folder, swap the app into place with a rename, and add a `quillsail` command. The [terminal installer guide](../docs/install-script.md) documents what they do for users. The site replaces `__QUILLSAIL_SITE_URL__` with its own address when serving them.

## The documentation site

[`docs.toml`](docs.toml) lists the pages in reading order. Pages are the repository's own Markdown files, rendered once at startup:

- Headings get GitHub's anchors, so links such as `desktop.md#multiple-publications` work on both GitHub and the site.
- Links to other pages become site links; links to other repository files go to GitHub.
- `> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]` and `> [!CAUTION]` blocks become callouts.
- Raw HTML isn't rendered.

To add a page, create it under `docs/`, add it to `docs.toml`, and run the tests. They fail if a file under `docs/` isn't listed, if a link points to a missing page or heading, or if a link on the website points to a missing docs page. The [reference pages](../docs/reference/) are generated from the application's code by `scripts/build_reference_docs.py`.

## Configuration

Everything is set with environment variables. Defaults suit local development.

| Variable | Default | Meaning |
| --- | --- | --- |
| `QUILLSAIL_SITE_URL` | `http://localhost:8000` | The site's public address |
| `QUILLSAIL_DOCS_URL` | `http://docs.localhost:8000` | The docs' public address. A path on the site's host, such as `https://quillsail.blog/docs`, also works. |
| `QUILLSAIL_UPDATES_URL` | `http://updates.localhost:8000` | The update address. Set it empty to turn it off. |
| `QUILLSAIL_PROXY_HOPS` | `0` | How many trusted reverse proxies are in front of the site |
| `QUILLSAIL_CHANNEL` | `beta` | The release channel the site offers by default |
| `QUILLSAIL_RELEASE_TTL` | `600` | Seconds between release checks |
| `QUILLSAIL_REPOSITORY` | `Dooba-Digital-LLC/QuillSail` | The GitHub repository for releases, edit links and source links |
| `QUILLSAIL_BRANCH` | `main` | The branch that edit and source links point to |
| `QUILLSAIL_METADATA_URL` | The `desktop-updates` release | Where the signed feed's metadata lives |
| `QUILLSAIL_TRUST_ROOT` | The bundled `root.json` | The trust root used to verify the feed |
| `QUILLSAIL_REPO_ROOT` | This repository | Where `docs/` and `website/docs.toml` are |
| `QUILLSAIL_RELEASES_FIXTURE` | Unset | A JSON file of releases, for development and tests only |
| `QUILLSAIL_OAUTH_RELAY_SECRET` | Unset | Persistent private signing/session secret of at least 32 characters for the OAuth confirmation relay |
| `QUILLSAIL_CLOUDFLARE_CLIENT_ID` | Unset | Registered public PKCE client ID, configured once for all QuillSail installations |
| `QUILLSAIL_CLOUDFLARE_SCOPES` | `dns.write zone.read argotunnel.write offline_access` | Exact approved base scopes for the shared client |
| `QUILLSAIL_CLOUDFLARE_EDGE_SCOPES` | Empty | Approved optional offline-fallback scopes, requested only when needed |
| `QUILLSAIL_GOOGLE_CLIENT_ID` | Unset | Shared Google Web application client ID for Gmail |
| `QUILLSAIL_GOOGLE_CLIENT_SECRET` | Unset | Confidential Google secret; keep only in the private backend environment |
| `QUILLSAIL_GMAIL_BROKER_SECRET` | Unset | Persistent private key of at least 32 characters for token-bound refresh proofs |
| `QUILLSAIL_GMAIL_ENABLED` | `0` | Set to `1` after callback and audience configuration; Google verification is required for public use |
| `QUILLSAIL_PRIVACY_CONTACT` | Unset | Operator-approved public privacy contact; enables the reviewed hosted-sign-in policy at `/privacy` |

A request for any other host, such as `www.quillsail.blog`, is redirected to the site's address.

With HTTPS, a relay secret and a client ID configured, `/.well-known/quillsail/cloudflare.json` publishes only the public client ID, scopes and canonical relay callback. Configure the registration once here; fresh desktop/server publications discover it when users click **Sign in with Cloudflare**. No client secret is needed for PKCE. Access tokens, refresh tokens and verifiers stay on the individual publication. Complete Cloudflare domain verification and promote the client to Public before inviting users outside its parent account. Custom app registrations and the API-token backup remain supported; see [hosting](../docs/hosting.md).

Shared Gmail uses a confidential Web client. Its secret never appears in `/.well-known/quillsail/gmail.json`; only the public ID, scope and HTTPS endpoints are published. Register the canonical `/oauth/gmail/callback`, configure the private Google credentials and persistent Gmail broker key, and activate the service only for the audience permitted by Google. A Testing app remains limited to listed test users; unrestricted public use requires Google verification for the sensitive send-only scope.

The callback uses browser-bound confirmation. `/oauth/gmail/token` accepts bounded server JSON requests for authorization-code exchange and proof-bound refresh, forwards only to Google's fixed token endpoint, and rejects broader mail scopes. User tokens transit the broker without being stored centrally; publications encrypt them locally and send email content directly to Google. Preserve the broker key across deployments and backups. Path-only logs, TLS and private environment permissions are required. See [email connectors](../docs/email-connectors.md).

The privacy-policy template remains unpublished until the operator reviews its statements and supplies `QUILLSAIL_PRIVACY_CONTACT`. It must match actual service operation before being linked from Google's consent screen. Keep the shared Gmail service disabled while required provider configuration or verification is incomplete, unless explicitly enabling access for Google's listed test users.

## Deploy

The site is a standard WSGI application: `gunicorn 'quillsail_site:create_app()'`. Build the container from the repository root, so the docs are included:

```sh
docker build -f website/Dockerfile -t quillsail-website .
docker run --rm -p 8000:8000 \
  -e QUILLSAIL_SITE_URL=https://quillsail.blog \
  -e QUILLSAIL_DOCS_URL=https://docs.quillsail.blog \
  -e QUILLSAIL_UPDATES_URL=https://updates.quillsail.blog \
  -e QUILLSAIL_PROXY_HOPS=1 \
  quillsail-website
```

Point all three host names (and `www`, if you want the redirect) at a reverse proxy that terminates HTTPS and forwards to port 8000 with the original `Host` header. With Caddy:

```text
quillsail.blog, www.quillsail.blog, docs.quillsail.blog, updates.quillsail.blog {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8000
}
```

The site sends its own security and caching headers: fingerprinted assets are cached for a year, pages for five minutes, and the release API for a minute. Rebuild and redeploy when `docs/` or the website changes; releases need no deploy.

## Search visibility

Set the three public URL variables to their final HTTPS addresses. The site redirects HTTP requests to those addresses, and pages use query-free canonical URLs, descriptive titles and summaries, social cards, and connected Schema.org data. Documentation and download pages include visible breadcrumbs. Release versions in the software metadata come from the verified feed.

The main site and docs each serve `/sitemap.xml` and advertise their sitemaps in `/robots.txt`. Search results, installers, release APIs, update feeds, health checks and errors carry `noindex` directives; crawlers can still fetch those URLs to read the directives. Static images remain indexable. HTML uses `Cache-Control: no-transform` to prevent proxy-injected scripts from conflicting with the site's security policy.

After deployment, verify the domain in [Google Search Console](https://search.google.com/search-console) and submit both public sitemaps:

- `https://quillsail.blog/sitemap.xml`
- `https://docs.quillsail.blog/sitemap.xml`

Use URL Inspection to check the homepage, download page and a documentation article. Search Console reports actual indexing and search traffic; analytics tools can help measure visitor behavior but do not directly improve rankings. Do not invent reviews, ratings or publication dates in structured data.

## Rebuild the font and images

The committed assets are generated from the repository's masters:

```sh
pip install -e 'website[assets]'
python website/scripts/build_fonts.py    # Amiri Bold, subset to Latin as WOFF2
python website/scripts/build_images.py   # social card, touch icon and favicons
```

## Tests

```sh
cd website
pytest
ruff check .
```

The tests build signed TUF repositories with throwaway keys to check release verification, render every page, check every docs link, and run `install.sh` end to end against a local copy of the site with a synthetic app. `install.ps1` needs PowerShell to run its tests.

## Graphical desktop downloads

New signed releases can include separate first-install targets at
`CHANNEL/installers/TARGET`: macOS `.dmg` (Apple Silicon and Intel), Windows x64
`-setup.exe`, and Linux x86-64 `.deb` for Ubuntu 22.04 and compatible Debian desktops.
The website verifies their signed names, versions, sizes and SHA-256 hashes and
prefers them for download buttons and `/download/TARGET`. Until available it
continues offering verified updater archives.

The release API keeps `downloads` and `/api/v1/releases/latest/TARGET` as updater
archives for existing terminal installers. `installers` in the release JSON and
`/api/v1/releases/latest/TARGET/installer` expose graphical installers separately.
Missing installer API entries return 503; they never fall back to unsigned assets.
Application removal preserves publication data. See the desktop guide for native
build dependencies, installation steps and signing limitations.

## Cloudflare OAuth confirmation relay

The site app includes `/oauth/cloudflare/callback`. Set `QUILLSAIL_OAUTH_RELAY_SECRET` to a persistent random secret of at least 32 characters to enable it. Deploy behind HTTPS and register that exact callback with a Cloudflare public PKCE client; see [hosting](../docs/hosting.md#cloudflare-oauth). The relay displays the destination and requires a CSRF-protected confirmation click. It does not exchange codes or receive provider tokens. Use query-free access logs at every proxy; the website container already uses a path-only format. Without configuration this route returns 503.
