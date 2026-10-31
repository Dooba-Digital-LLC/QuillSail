"""quillsail.blog: the homepage, downloads, install scripts and release API."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from flask import Blueprint, Flask, abort, current_app, jsonify, redirect, render_template, request

from quillsail_site.config import CHANNELS, Config
from quillsail_site.docs import Docs
from quillsail_site.platforms import PLATFORMS, detect
from quillsail_site.releases import Build, Catalog, Release
from quillsail_site.seo import (
    DOWNLOAD_DESCRIPTION,
    DOWNLOAD_TITLE,
    HOME_DESCRIPTION,
    HOME_TITLE,
    page_metadata,
)
from quillsail_site.web import make_app, static_file, text_response

INSTALLERS = Path(__file__).resolve().parents[1] / "install"
bp = Blueprint("site", __name__)


def create_site_app(config: Config, catalog: Catalog, docs: Docs) -> Flask:
    app = make_app("site", config, form_origins=(f"{config.docs.scheme}://{config.docs.host}",))
    app.extensions["catalog"], app.extensions["docs"] = catalog, docs
    site = config.site.url
    app.extensions["installers"] = {
        name: (INSTALLERS / name).read_text(encoding="utf-8").replace("__QUILLSAIL_SITE_URL__", site)
        for name in ("install.sh", "install.ps1")
    }
    app.jinja_env.globals.update(
        platforms=PLATFORMS,
        install_commands={"sh": f"curl -fsSL {site}/install.sh | bash", "ps1": f"irm {site}/install.ps1 | iex"},
    )
    from quillsail_site.views.oauth import bp as oauth_bp

    app.config.update(SECRET_KEY=config.oauth_relay_secret or None, MAX_CONTENT_LENGTH=32 * 1024,
                      SESSION_COOKIE_NAME='quillsail_oauth_relay', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=config.site.scheme == 'https', SESSION_COOKIE_SAMESITE='Lax')
    app.register_blueprint(oauth_bp)
    app.register_blueprint(bp)
    return app


def catalog() -> Catalog:
    return current_app.extensions["catalog"]


def requested_channel() -> str:
    channel = request.args.get("channel") or current_app.config["QUILLSAIL"].channel
    if channel not in CHANNELS:
        abort(400, description="Choose the stable or beta channel.")
    return channel


def page_context(release: Release | None) -> dict:
    detected = detect(request.user_agent.string)
    build = release.downloads.get(detected) if release and detected else None
    return {"release": release, "detected": detected, "build": build,
            "platform": PLATFORMS.get(detected) if build else None}


@bp.get("/")
def home():
    release = catalog().release()
    config = current_app.config["QUILLSAIL"]
    metadata = page_metadata(config, title=HOME_TITLE, summary=HOME_DESCRIPTION,
                             url=config.site.join("/"), release=release)
    response = current_app.make_response(render_template("site/home.html", **page_context(release), **metadata))
    response.vary.add("User-Agent")
    return response


@bp.get("/download")
def downloads():
    release = catalog().release()
    config = current_app.config["QUILLSAIL"]
    metadata = page_metadata(config, title=DOWNLOAD_TITLE, summary=DOWNLOAD_DESCRIPTION,
                             url=config.site.join("/download"), release=release, download=True)
    context = page_context(release)
    started = request.args.get("started")
    started_build = release.downloads.get(started) if release and started in PLATFORMS else None
    response = current_app.make_response(render_template(
        "site/download.html", started=started_build, started_platform=PLATFORMS.get(started) if started_build else None,
        **context, **metadata))
    response.vary.add("User-Agent")
    return response


@bp.get("/download/<target>")
def download(target: str):
    """A stable address for the newest download, such as /download/macos-arm64."""
    if target not in PLATFORMS and target != "server":
        abort(404)
    release = catalog().release(requested_channel())
    build = (release.server if target == "server" else release.downloads.get(target)) if release else None
    response = redirect(build.url if build else current_app.config["QUILLSAIL"].releases_url, 302)
    response.cache_control.public = True
    response.cache_control.max_age = 60
    return response


@bp.get("/install.sh")
@bp.get("/install.ps1")
def installer():
    name = request.path.rsplit("/", 1)[-1]
    return text_response(current_app.extensions["installers"][name], mimetype="text/plain", max_age=300)


# --- release API --------------------------------------------------------------------------------


def build_json(build: Build) -> dict:
    data = {"version": build.version, "archive": build.archive, "size": build.size, "sha256": build.sha256,
            "url": build.url}
    if build.target in PLATFORMS:
        platform = PLATFORMS[build.target]
        data.update(system=platform.system, variant=platform.variant)
    return data


def unavailable():
    response = jsonify(error="Release information is unavailable right now. Try again in a few minutes.")
    response.status_code, response.headers["Retry-After"] = 503, "60"
    response.cache_control.no_store = True
    return response


def api_response(data: dict):
    response = jsonify(data)
    response.cache_control.public = True
    response.cache_control.max_age = 60
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@bp.get("/api/v1/releases/latest")
def latest_release():
    release = catalog().release(requested_channel())
    if release is None:
        return unavailable()
    return api_response({
        "channel": release.channel, "version": release.version, "notes_url": release.notes_url,
        "checked_at": release.checked_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "verified_by": release.source,
        "downloads": {target: build_json(build) for target, build in release.builds.items()},
        "installers": {target: build_json(build) for target, build in release.installers.items()},
        "server": build_json(release.server) if release.server else None,
    })


@bp.get("/api/v1/releases/latest/<name>/installer")
@bp.get("/api/v1/releases/latest/<name>")
def latest_build(name: str):
    """One download. The `.txt` form is easy to read from a shell script."""
    target, text = (name[:-4], True) if name.endswith(".txt") else (name, False)
    if target not in PLATFORMS and target != "server":
        abort(404)
    release = catalog().release(requested_channel())
    build = (release.server if target == "server" else release.builds.get(target)) if release else None
    if request.path.endswith("/installer"):
        build = release.installers.get(target) if release else None
    if build is None:
        if text:
            response = text_response("error Release information is unavailable right now. Try again in a few minutes.\n",
                                     max_age=0)
            response.status_code, response.headers["Retry-After"] = 503, "60"
            response.cache_control.no_store = True
            return response
        return unavailable()
    if not text:
        return api_response({"channel": release.channel, "target": target, **build_json(build)})
    lines = [("version", build.version), ("channel", release.channel), ("target", target), ("archive", build.archive),
             ("size", str(build.size)), ("sha256", build.sha256), ("url", build.url)]
    response = text_response("".join(f"{key} {value}\n" for key, value in lines), max_age=60)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


# --- housekeeping -------------------------------------------------------------------------------


@bp.get("/healthz")
def health():
    from quillsail_site import __version__

    status = catalog().status()
    if status["checked_at"]:
        status["checked_at"] = status["checked_at"].isoformat().replace("+00:00", "Z")
    response = jsonify(status="ok", website=__version__, releases=status)
    response.cache_control.no_store = True
    return response


@bp.get("/robots.txt")
def robots():
    config = current_app.config["QUILLSAIL"]
    return text_response(
        f"User-agent: *\nAllow: /\nSitemap: {config.site.join('/sitemap.xml')}\n"
        f"Sitemap: {config.docs.join('/sitemap.xml')}\n", max_age=86_400)


@bp.get("/sitemap.xml")
def sitemap():
    site = current_app.config["QUILLSAIL"].site
    body = render_template("sitemap.xml", urls=[site.join("/"), site.join("/download")])
    return text_response(body, mimetype="application/xml", max_age=3600)


@bp.get("/favicon.ico")
def favicon():
    return static_file("img/favicon.ico")


@bp.get("/apple-touch-icon.png")
def touch_icon():
    return static_file("img/apple-touch-icon.png")


@bp.get("/docs")
@bp.get("/changelog")
def docs_shortcut():
    config = current_app.config["QUILLSAIL"]
    return redirect(config.docs.join("/changelog" if request.path == "/changelog" else "/"), 301)


@bp.get("/github")
def github_shortcut():
    return redirect(current_app.config["QUILLSAIL"].github_url, 301)
