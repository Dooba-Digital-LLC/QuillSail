"""updates.quillsail.blog: a stable address for the signed update feed.

The feed and the archives stay on GitHub Releases; this host redirects to them.
Clients verify everything with TUF, so the redirect needs no trust of its own.
Only exact metadata and archive names are accepted, so it can't be used to
redirect anywhere else.

Point the app here by setting METADATA_URL in copydesk/updater.py to
https://updates.quillsail.blog/ and the archive base to
https://updates.quillsail.blog/v{version}/.
"""

from __future__ import annotations

import re

from flask import Blueprint, Flask, abort, current_app, jsonify, redirect

from quillsail_site.config import Config
from quillsail_site.web import make_app, text_response

bp = Blueprint("updates", __name__)
METADATA = re.compile(r"(?:timestamp|[1-9][0-9]{0,11}\.(?:root|snapshot|targets))\.json")
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+"


def create_updates_app(config: Config) -> Flask:
    app = make_app("updates", config)
    app.register_blueprint(bp)
    return app


def archive_pattern(version: str) -> re.Pattern:
    escaped = re.escape(version)
    return re.compile(rf"QuillSail-{escaped}-(?:macos-arm64|macos-x86_64|windows-x86_64)\.zip"
                      rf"|QuillSail-{escaped}-linux-x86_64\.tar\.gz|copydesk-{escaped}-py3-none-any\.whl")


def no_cache_redirect(url: str):
    response = redirect(url, 302)
    response.cache_control.no_cache = True
    return response


@bp.get("/")
def index():
    config: Config = current_app.config["QUILLSAIL"]
    return text_response(
        "QuillSail update service\n\n"
        "This address serves QuillSail's signed update metadata. The desktop app and\n"
        "`quillsail update` verify every file before installing it.\n\n"
        f"Documentation: {config.docs.join('/desktop-updates')}\n", max_age=3600)


@bp.get("/<name>")
def metadata(name: str):
    if not METADATA.fullmatch(name):
        abort(404)
    return no_cache_redirect(current_app.config["QUILLSAIL"].feed_url + name)


@bp.get("/v<version>/<archive>")
def archive(version: str, archive: str):
    if not re.fullmatch(VERSION, version) or not archive_pattern(version).fullmatch(archive):
        abort(404)
    return no_cache_redirect(f"{current_app.config['QUILLSAIL'].releases_url}/download/v{version}/{archive}")


@bp.get("/healthz")
def health():
    response = jsonify(status="ok")
    response.cache_control.no_store = True
    return response
