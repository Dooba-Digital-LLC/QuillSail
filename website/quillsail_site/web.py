"""What the site, docs and updates apps share: templates, assets, headers and errors."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from flask import Flask, Response, render_template, request, url_for

from quillsail_site import __version__
from quillsail_site.config import Config

PACKAGE = Path(__file__).resolve().parent
STATIC = PACKAGE / "static"
YEAR = 31_536_000


@lru_cache(maxsize=256)
def _fingerprint(filename: str) -> str:
    return hashlib.sha256((STATIC / filename).read_bytes()).hexdigest()[:10]


def make_app(name: str, config: Config, *, form_origins: tuple[str, ...] = ()) -> Flask:
    app = Flask(name, template_folder=str(PACKAGE / "templates"), static_folder=str(STATIC),
                static_url_path="/static")
    app.config.update(SEND_FILE_MAX_AGE_DEFAULT=YEAR, JSON_SORT_KEYS=False, QUILLSAIL=config)
    app.jinja_env.trim_blocks = app.jinja_env.lstrip_blocks = True

    def asset(filename: str) -> str:
        return url_for("static", filename=filename, v=_fingerprint(filename))

    app.jinja_env.globals.update(
        asset=asset, site_url=config.site.join, docs_url=config.docs.join, config=config,
        github_url=config.github_url, website_version=__version__,
    )
    app.jinja_env.filters["datetime_label"] = lambda value: f"{value:%B} {value.day}, {value:%Y} at {value:%H:%M} UTC"

    # Pages load only their own fonts, styles and scripts: no inline code or third parties.
    csp = "; ".join([
        "default-src 'none'", "script-src 'self'", "style-src 'self'", "img-src 'self'", "font-src 'self'",
        "connect-src 'self'", "manifest-src 'self'", "base-uri 'none'", "frame-ancestors 'none'",
        "form-action " + " ".join(("'self'", *form_origins)),
    ])

    @app.after_request
    def headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), browsing-topics=()")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if response.mimetype == "text/html":
            response.headers.setdefault("Content-Security-Policy",
                                        csp + ("; upgrade-insecure-requests" if request.is_secure else ""))
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.endpoint == "static" and "v" not in request.args:
            # Unversioned asset URLs (fonts referenced from CSS) revalidate daily.
            response.cache_control.max_age = 86_400
            response.cache_control.public = True
        elif request.endpoint == "static":
            response.cache_control.immutable = True
            response.cache_control.public = True
        elif "Cache-Control" not in response.headers and response.status_code == 200:
            response.cache_control.public = True
            response.cache_control.max_age = 300
        return response

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html", app_name=name), 404

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("errors/500.html", app_name=name), 500

    return app


def text_response(body: str, *, mimetype: str = "text/plain", max_age: int = 300) -> Response:
    response = Response(body, mimetype=mimetype)
    response.cache_control.public = True
    response.cache_control.max_age = max_age
    return response


def static_file(path: str, *, max_age: int = 86_400) -> Response:
    """Serve a file from static/ at a fixed address such as /favicon.ico."""
    from flask import send_from_directory

    response = send_from_directory(STATIC, path, max_age=max_age)
    response.cache_control.public = True
    return response
