"""docs.quillsail.blog: documentation pages and search."""

from __future__ import annotations

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from quillsail_site.config import Config
from quillsail_site.docs import Docs
from quillsail_site.web import make_app, static_file, text_response

bp = Blueprint("docs", __name__)


def create_docs_app(config: Config, docs: Docs) -> Flask:
    app = make_app("docs", config)
    app.extensions["docs"] = docs
    app.jinja_env.globals.update(docs=docs)
    app.register_blueprint(bp)
    return app


def docs() -> Docs:
    return current_app.extensions["docs"]


@bp.get("/", defaults={"slug": ""})
@bp.get("/<path:slug>")
def page(slug: str):
    # Tidy addresses copied from GitHub or typed with a trailing slash.
    if slug.endswith(".md") or (slug.endswith("/") and slug.strip("/")):
        cleaned = slug.removesuffix("/").removesuffix(".md").removeprefix("docs/")
        cleaned = "" if cleaned.lower() == "readme" else cleaned
        if docs().get(cleaned):
            return redirect(url_for("docs.page", slug=cleaned), 301)
    found = docs().get(slug)
    if found is None:
        abort(404)
    return render_template("docs/page.html", page=found)


@bp.get("/search")
def search():
    query = request.args.get("q", "").strip()[:200]
    results = docs().search(query) if query else []
    response = current_app.make_response(render_template("docs/search.html", query=query, results=results))
    response.cache_control.public = True
    response.cache_control.max_age = 300
    return response


@bp.get("/search.json")
def search_json():
    query = request.args.get("q", "").strip()[:200]
    results = docs().search(query, limit=8) if query else []
    response = jsonify(query=query, results=[
        {"title": result.page.title, "section": result.page.section, "heading": result.heading,
         "url": url_for("docs.page", slug=result.page.slug) + (f"#{result.anchor}" if result.anchor else ""),
         "snippet": str(result.snippet)}
        for result in results
    ])
    response.cache_control.public = True
    response.cache_control.max_age = 300
    return response


@bp.get("/robots.txt")
def robots():
    sitemap = current_app.config["QUILLSAIL"].docs.join("/sitemap.xml")
    return text_response(f"User-agent: *\nDisallow: /search\nSitemap: {sitemap}\n", max_age=86_400)


@bp.get("/sitemap.xml")
def sitemap():
    address = current_app.config["QUILLSAIL"].docs
    urls = [address.join(page.path) for section in docs().sections for page in section.pages]
    return text_response(render_template("sitemap.xml", urls=urls), mimetype="application/xml", max_age=3600)


@bp.get("/favicon.ico")
def favicon():
    return static_file("img/favicon.ico")


@bp.get("/apple-touch-icon.png")
def touch_icon():
    return static_file("img/apple-touch-icon.png")
