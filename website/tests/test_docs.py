"""The documentation site: every page renders, every link resolves, search finds things."""

import re
from urllib.parse import urlsplit

import pytest

from conftest import MAC_UA, WINDOWS_UA
from quillsail_site.markdown import Renderer, github_slug


def test_every_docs_file_is_in_the_navigation(docs):
    assert docs.unlisted_files() == [], "Add these to website/docs.toml, or to its exclude list"


def test_every_link_between_pages_resolves(docs):
    assert docs.broken_links == []


def test_every_page_renders(docs, docs_client):
    for section in docs.sections:
        for item in section.pages:
            response = docs_client.get("/" + item.slug)
            assert response.status_code == 200, item.file
            html = response.get_data(as_text=True)
            assert html.count("<h1>") == 1, item.file
            assert 'aria-current="page"' in html
            assert "<script>" not in html


def test_website_links_into_the_docs_resolve(docs, site):
    """Links from the homepage and download page must point to real pages and headings."""
    checked = 0
    for path, agent in (("/", MAC_UA), ("/download", WINDOWS_UA), ("/download?started=linux-x86_64", MAC_UA)):
        html = site.get(path, headers={"User-Agent": agent}).get_data(as_text=True)
        for href in re.findall(r'href="(https://docs\.quillsail\.test[^"]*)"', html):
            parts = urlsplit(href)
            target = docs.get(parts.path.lstrip("/"))
            assert target is not None, href
            if parts.fragment:
                assert parts.fragment in {heading.id for heading in target.headings}, href
            checked += 1
    assert checked > 10


def test_pages_have_titles_descriptions_and_edit_links(docs_client):
    html = docs_client.get("/install-script").get_data(as_text=True)
    assert "<title>Terminal installer · QuillSail Docs</title>" in html
    assert '<link rel="canonical" href="https://docs.quillsail.test/install-script">' in html
    assert re.search(r'<meta name="description" content="Install the QuillSail desktop app', html)
    assert "https://github.com/DoobaDigital/QuillSail/edit/main/docs/install-script.md" in html
    assert 'rel="prev"' in html and 'rel="next"' in html


def test_github_style_addresses_redirect(docs_client):
    assert docs_client.get("/desktop.md").location == "/desktop"
    assert docs_client.get("/desktop/").location == "/desktop"
    assert docs_client.get("/README.md").location == "/"


def test_search(docs, docs_client):
    results = docs.search("one-click unsubscribe")
    assert results and results[0].page.slug in {"subscribers", "email", "security"}
    assert "<mark>" in results[0].snippet
    html = docs_client.get("/search?q=setup+code").get_data(as_text=True)
    assert "Results for “setup code”" in html
    assert '<meta name="robots" content="noindex">' in html
    data = docs_client.get("/search.json?q=quillsail+update").get_json()
    assert 0 < len(data["results"]) <= 8
    assert {"title", "section", "heading", "url", "snippet"} <= set(data["results"][0])
    assert docs.search("") == [] and docs.search("x") == []


def test_search_snippets_are_escaped(docs):
    for result in docs.search("script"):
        assert "<script" not in str(result.snippet)


@pytest.mark.parametrize(("heading", "slug"), [
    ("Multiple publications", "multiple-publications"),
    ("Register a Python/systemd server once", "register-a-pythonsystemd-server-once"),
    ("`quillsail update` and rollback", "quillsail-update-and-rollback"),
    ("What's new?", "whats-new"),
    ("Outlook and Microsoft 365", "outlook-and-microsoft-365"),
])
def test_heading_anchors_match_github(heading, slug):
    assert github_slug(heading) == slug


def test_markdown_features():
    rendered = Renderer().render(
        "# Title\n\nIntro with [a link](other.md#part).\n\n## Part\n\n## Part\n\n"
        "> [!WARNING]\n> Careful **now**.\n\n> A plain quote.\n\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n```sh\necho hi\n```\n\n<div>raw</div>\n",
        lambda href: "/resolved" if href.startswith("other.md") else href,
    )
    assert rendered.title == "Title"
    assert "<h1" not in rendered.html
    assert [heading.id for heading in rendered.headings] == ["part", "part-1"]
    assert '<a href="/resolved">a link</a>' in rendered.html
    assert '<aside class="callout callout-warning" aria-label="Warning"><p class="callout-label">Warning</p>' in rendered.html
    assert "[!WARNING]" not in rendered.html
    assert "<blockquote>" in rendered.html
    assert '<div class="table-wrap" tabindex="0"><table>' in rendered.html
    assert '<figcaption class="code-label">Shell</figcaption>' in rendered.html
    assert "<div>raw</div>" not in rendered.html
    assert rendered.summary == "Intro with a link."


def test_reference_pages_are_generated(docs):
    for slug in ("reference/cli", "reference/settings", "reference/permissions", "reference/workflow",
                 "reference/hooks", "reference/configuration"):
        page = docs.get(slug)
        assert page is not None and "scripts/build_reference_docs.py" in page.html


def test_docs_home_lists_every_section(docs_client, docs):
    html = docs_client.get("/").get_data(as_text=True)
    for section in docs.sections:
        assert f'<h3 class="kicker">{section.title}</h3>' in html
