# Installation

QuillSail runs as a Python web application with a persistent database and a background worker. Use Docker Compose for a new installation, or install the Python package behind your own reverse proxy. GitHub hosts the source, CI, release files and optional container images; run the application on a server you control.

## Docker Compose

Install Docker Engine/Desktop with the Compose plugin, then clone or extract this repository. From its root:

```sh
cp deploy/compose.env.example deploy/compose.env
# Edit deploy/compose.env for your hostname and port.
docker compose --env-file deploy/compose.env up -d --build
```

Open `http://localhost:8080`. The initializer creates the schema and a protected configuration with a random secret key on the persistent volume. It creates no default administrator or demo account. The browser asks for a setup code to claim the installation. After opening the setup page, retrieve that code locally:

```sh
docker compose --env-file deploy/compose.env exec web cat /var/lib/copydesk/data/setup-code.txt
```

Create the first administrator and choose Newsletter, Blog, or Blog + newsletter. The separate worker handles queued email. Delivery initially uses the file backend, so configure a real sender in Settings → Email before expecting inbox delivery. Gmail/Outlook require the one-time provider registration described in [email connectors](email-connectors.md).

The image runs as numeric user/group `10001:10001`. The named `copydesk-data` volume holds configuration, SQLite data, uploads, themes and extensions. Preserve the entire volume. Web and worker containers share it; neither container runs migrations at startup. The dedicated init service performs initialization/migrations before they start. Additional publications need separate Compose project names, ports and volumes.

The web port is bound to loopback. For a public site, set `COPYDESK_BASE_URL` to its exact HTTPS address, including any path prefix, and set `COPYDESK_PROXY_HOPS` to the number of trusted reverse proxies. Put a TLS proxy in front of the loopback port. [deploy/Caddyfile.example](../deploy/Caddyfile.example) is a minimal host-side Caddy example; see [Caddy's documentation](https://caddyserver.com/docs/automatic-https) for DNS and certificate requirements. Restart the containers after changing environment settings.

For example, a subpath installation can use `https://example.org/publication` as its base URL. Forward that path unchanged to QuillSail; the application handles its own prefix. QuillSail supports running at `/` as well.

Useful commands:

```sh
docker compose --env-file deploy/compose.env ps
docker compose --env-file deploy/compose.env logs --tail=100 web worker
docker compose --env-file deploy/compose.env exec web copydesk check
docker compose --env-file deploy/compose.env exec worker copydesk jobs list
```

Web health checks query the database through `/healthz`. Worker checks inspect its recent database heartbeat. These checks do not prove inbox delivery. Configuration and setup codes are private; do not paste them into public issues.

## Published container images

After maintainers publish a GitHub release, the release workflow can publish `ghcr.io/YOUR_ACCOUNT/copydesk:VERSION`. Replace the owner, repository and version with the real values. Set `COPYDESK_IMAGE` in your local Compose environment file, then use `docker compose --env-file deploy/compose.env pull` and `up -d --no-build`. This repository does not yet identify a published image. The configured release image is initially built/tested for Linux amd64; other architectures can build from source and need their own verification.

## Python installation

Use Python 3.11+ in a virtual environment. From the source checkout or extracted source archive:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install '.[server]'
copydesk init --dir ./instance --site-name 'My publication' --mode hybrid --base-url http://localhost:5055
copydesk --config ./instance/copydesk.toml serve --port 5055
```

Initialization prompts for the first administrator and writes a configuration with mode 0600. `serve` is for local development. Choose `--mode newsletter`, `blog`, or `hybrid`; hosts can change mode later in General settings. There is no frontend build step.

For production Linux, use a dedicated `copydesk` account, a versioned virtual environment such as `/opt/copydesk/current/venv`, and instance files under `/var/lib/copydesk`. Initialize the instance as that account with your public HTTPS base URL and `--behind-proxy` when there is one trusted proxy. Copy the example [web](../deploy/systemd/copydesk-web.service) and [worker](../deploy/systemd/copydesk-worker.service) units into `/etc/systemd/system/`, adjust their paths/port to your installation, then enable them. The example web server binds loopback port 8000; proxy HTTPS requests to it. Keep instance directories private and writable only by the service account.

To install a downloaded release wheel, use `pip install '/path/to/copydesk-VERSION-py3-none-any.whl[server]'`. Add the `postgres` or `mysql` extra for those database drivers. Source/Compose installations use SQLite by default; an external database is configured in the protected instance TOML or process environment. See [configuration](configuration.md).

## Backups and upgrades

Stop web and worker before backing up or migrating. For Compose, preserve the complete named volume with Docker's volume-backup facilities, or export application data and separately protect the instance configuration/uploads. Follow [maintenance](maintenance.md); the instance secret key is required to decrypt saved email credentials after restore.

For a Compose source upgrade, select the reviewed release, stop web/worker, take a backup, build the new image, run the init service, and start the services:

```sh
docker compose --env-file deploy/compose.env stop web worker
# Back up the complete instance volume now.
docker compose --env-file deploy/compose.env build
docker compose --env-file deploy/compose.env run --rm --no-deps init
docker compose --env-file deploy/compose.env up -d --no-build
```

If using registry images, pull the desired pinned version instead of building. Keep the old image/package together with its matching database backup for recovery. Do not delete a publication's volume during an ordinary upgrade.
