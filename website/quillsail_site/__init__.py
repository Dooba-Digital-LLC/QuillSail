"""The QuillSail website: quillsail.blog, docs.quillsail.blog and updates.quillsail.blog.

One process serves all three. Requests are routed by host (and optional path
prefix), so the same build runs on separate domains in production and on
`localhost` subdomains in development. Run it with:

    gunicorn 'quillsail_site:create_app()'
"""

from __future__ import annotations

__version__ = "1.0.0"


class HostDispatcher:
    """Send each request to the app whose public address matches its host and path."""

    def __init__(self, routes, fallback):
        # Longest path prefix first, so /docs wins over / on a shared host.
        self.routes = sorted(routes, key=lambda route: len(route[0].prefix), reverse=True)
        self.fallback = fallback

    @staticmethod
    def _host(environ) -> str:
        host = (environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or "").lower()
        scheme = environ.get("wsgi.url_scheme", "http")
        default = {"http": ":80", "https": ":443"}[scheme] if scheme in {"http", "https"} else ""
        return host[: -len(default)] if default and host.endswith(default) else host

    def __call__(self, environ, start_response):
        host, path = self._host(environ), environ.get("PATH_INFO", "") or "/"
        for address, app in self.routes:
            if host != address.host:
                continue
            prefix = address.prefix
            if not prefix:
                return app(environ, start_response)
            if path == prefix or path.startswith(prefix + "/"):
                environ = dict(environ, SCRIPT_NAME=environ.get("SCRIPT_NAME", "") + prefix,
                               PATH_INFO=path[len(prefix):] or "/")
                return app(environ, start_response)
        return self.fallback(environ, start_response)


def create_app(config=None, *, catalog=None, docs=None):
    """Build the whole website. Arguments replace the defaults in tests."""
    from flask import Flask, jsonify, redirect, request
    from werkzeug.middleware.proxy_fix import ProxyFix

    from quillsail_site.config import Config
    from quillsail_site.docs import Docs
    from quillsail_site.releases import Catalog
    from quillsail_site.views.docs import create_docs_app
    from quillsail_site.views.site import create_site_app
    from quillsail_site.views.updates import create_updates_app

    config = config or Config.from_env()
    catalog = catalog or Catalog(config)
    docs = docs or Docs(config.repo_root, repository=config.repository, branch=config.branch,
                        prefix=config.docs.prefix)
    apps = {"site": create_site_app(config, catalog, docs), "docs": create_docs_app(config, docs)}
    routes = [(config.site, apps["site"]), (config.docs, apps["docs"])]
    if config.updates:
        apps["updates"] = create_updates_app(config)
        routes.append((config.updates, apps["updates"]))

    # Any other host (www., a raw IP, an old domain) goes to the canonical site.
    fallback = Flask("fallback")

    @fallback.route("/healthz")
    def health():
        return jsonify(status="ok")

    @fallback.route("/", defaults={"path": ""})
    @fallback.route("/<path:path>")
    def canonical(path):
        query = request.query_string.decode("latin-1")
        return redirect(config.site.join("/" + path) + (f"?{query}" if query else ""), 301)

    application = HostDispatcher(routes, fallback)
    application.apps, application.catalog, application.docs, application.config = apps, catalog, docs, config
    wsgi = application
    if config.proxy_hops:
        hops = config.proxy_hops
        wsgi = ProxyFix(application, x_for=hops, x_proto=hops, x_host=hops, x_port=hops, x_prefix=0)
        wsgi.apps, wsgi.catalog, wsgi.docs, wsgi.config = apps, catalog, docs, config
    if config.warm_releases:
        catalog.refresh_in_background()
    return wsgi
