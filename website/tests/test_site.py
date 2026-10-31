"""The homepage, downloads, installers, release API, hosts and headers."""

import json
import re

import pytest
from werkzeug.test import Client

from conftest import IPHONE_UA, LINUX_UA, MAC_UA, WINDOWS_UA
from quillsail_site import create_app
from quillsail_site.config import Config, ConfigError


def page(client, path, agent=MAC_UA):
    response = client.get(path, headers={"User-Agent": agent})
    assert response.status_code == 200, path
    return response.get_data(as_text=True)


# --- homepage and downloads -----------------------------------------------------------------------


def test_homepage_has_the_headline_download_and_terminal_install(site):
    html = page(site, "/")
    assert "<h1 id=\"hero-title\" class=\"display\">Where readers write.</h1>" in html
    assert "Download for macOS" in html
    assert "Apple silicon · Version 2.3.4 beta" in html
    assert 'href="/download?started=macos-arm64"' in html
    assert "Download for Intel Mac" in html
    assert "curl -fsSL https://quillsail.test/install.sh | bash" in html
    assert "irm https://quillsail.test/install.ps1 | iex" in html
    assert html.count("<h1") == 1


@pytest.mark.parametrize(("agent", "label", "selected"), [
    (WINDOWS_UA, "Download for Windows", "ps1"),
    (LINUX_UA, "Download for Linux", "sh"),
])
def test_homepage_offers_the_visitors_download(site, agent, label, selected):
    html = page(site, "/", agent)
    assert label in html
    assert "Download for Intel Mac" not in html
    # The visitor's terminal tab is the one shown first.
    assert re.search(rf'id="install-tab-{selected}"[^>]*aria-selected="true"', html)


def test_phones_get_every_download_rather_than_a_guess(site):
    html = page(site, "/", IPHONE_UA)
    assert "Download QuillSail" in html
    assert "For Mac, Windows and Linux computers" in html


def test_pages_vary_by_browser_for_download_detection(site):
    response = site.get("/", headers={"User-Agent": MAC_UA})
    assert "User-Agent" in response.headers["Vary"]


def test_download_page_lists_every_download_with_checksums(site):
    html = page(site, "/download", WINDOWS_UA)
    for name in ("QuillSail-2.3.4-macos-arm64.zip", "QuillSail-2.3.4-macos-x86_64.zip",
                 "QuillSail-2.3.4-windows-x86_64.zip", "QuillSail-2.3.4-linux-x86_64.tar.gz",
                 "copydesk-2.3.4-py3-none-any.whl"):
        assert name in html
    assert "This computer" not in html  # Added by CSS, not markup.
    assert 'class="is-detected"' in html
    assert "SHA256SUMS.txt" in html


def test_starting_a_download_shows_next_steps_and_redirects(site):
    html = page(site, "/download?started=windows-x86_64")
    assert '<meta http-equiv="refresh" content="1; url=/download/windows-x86_64">' in html
    assert '<meta name="robots" content="noindex">' in html
    assert "Extract All" in html
    assert "Your download is starting" in html


def test_unknown_started_platform_is_ignored(site):
    html = page(site, "/download?started=amiga")
    assert "Your download is starting" not in html
    assert 'http-equiv="refresh"' not in html


def test_download_addresses_redirect_to_the_newest_file(site):
    response = site.get("/download/linux-x86_64")
    assert response.status_code == 302
    assert response.location == "https://downloads.example.test/QuillSail-2.3.4-linux-x86_64.tar.gz"
    assert site.get("/download/server").location.endswith("copydesk-2.3.4-py3-none-any.whl")
    assert site.get("/download/amiga").status_code == 404
    assert site.get("/download/macos-arm64?channel=nightly").status_code == 400


def test_without_release_details_the_site_points_to_github(config, docs, fixture_file):
    fixture_file.write_text(json.dumps({"channels": {}}))
    client = create_app(config, docs=docs).apps["site"].test_client()
    html = page(client, "/")
    assert "Download from GitHub" in html
    assert "https://github.com/DoobaDigital/QuillSail/releases" in html
    assert client.get("/download/macos-arm64").location == "https://github.com/DoobaDigital/QuillSail/releases"
    response = client.get("/api/v1/releases/latest")
    assert response.status_code == 503 and response.headers["Retry-After"] == "60"
    text = client.get("/api/v1/releases/latest/macos-arm64.txt")
    assert text.status_code == 503 and text.get_data(as_text=True).startswith("error ")


# --- installers and API ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installers_are_served_with_the_site_address(site, name):
    response = site.get(f"/{name}")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    body = response.get_data(as_text=True)
    assert "__QUILLSAIL_SITE_URL__" not in body
    assert "https://quillsail.test" in body
    assert response.cache_control.max_age == 300


def test_release_api(site):
    data = site.get("/api/v1/releases/latest").get_json()
    assert data["version"] == "2.3.4" and data["channel"] == "beta"
    assert set(data["downloads"]) == {"macos-arm64", "macos-x86_64", "windows-x86_64", "linux-x86_64"}
    assert data["downloads"]["macos-arm64"]["system"] == "macOS"
    assert re.fullmatch(r"[0-9a-f]{64}", data["downloads"]["windows-x86_64"]["sha256"])
    assert data["server"]["archive"] == "copydesk-2.3.4-py3-none-any.whl"
    one = site.get("/api/v1/releases/latest/windows-x86_64").get_json()
    assert one["archive"] == "QuillSail-2.3.4-windows-x86_64.zip" and one["target"] == "windows-x86_64"
    assert site.get("/api/v1/releases/latest/amiga").status_code == 404


def test_release_text_for_shell_scripts(site):
    response = site.get("/api/v1/releases/latest/macos-arm64.txt")
    lines = dict(line.split(" ", 1) for line in response.get_data(as_text=True).splitlines())
    assert lines["version"] == "2.3.4"
    assert lines["archive"] == "QuillSail-2.3.4-macos-arm64.zip"
    assert lines["url"] == "https://downloads.example.test/QuillSail-2.3.4-macos-arm64.zip"
    assert re.fullmatch(r"[0-9a-f]{64}", lines["sha256"]) and int(lines["size"]) > 0
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_health(site):
    # Health checks never wait on GitHub: before any release check, they report none.
    assert site.get("/healthz").get_json()["releases"]["available"] is False
    site.get("/")
    data = site.get("/healthz").get_json()
    assert data["status"] == "ok"
    assert data["releases"]["channels"] == {"beta": "2.3.4"}
    assert data["releases"]["source"] == "fixture"


# --- hosts ---------------------------------------------------------------------------------------


def test_requests_are_routed_by_host(website):
    client = Client(website)
    assert "Where readers write." in client.get("/", base_url="https://quillsail.test").get_data(as_text=True)
    assert "QuillSail documentation" in client.get("/", base_url="https://docs.quillsail.test").get_data(as_text=True)
    assert "update service" in client.get("/", base_url="https://updates.quillsail.test").get_data(as_text=True)


def test_other_hosts_redirect_to_the_site(website):
    client = Client(website)
    response = client.get("/download?started=linux-x86_64", base_url="https://www.quillsail.test")
    assert response.status_code == 301
    assert response.location == "https://quillsail.test/download?started=linux-x86_64"
    assert client.get("/healthz", base_url="http://10.0.0.5:8000").status_code == 200


def test_docs_can_live_under_a_path_on_the_site(fixture_file, docs):
    from quillsail_site.docs import Docs

    config = Config.from_env({"QUILLSAIL_SITE_URL": "https://quillsail.test",
                              "QUILLSAIL_DOCS_URL": "https://quillsail.test/docs", "QUILLSAIL_UPDATES_URL": ""},
                             releases_fixture=fixture_file, warm_releases=False)
    prefixed = Docs(docs.root, repository=docs.repository, prefix="/docs")
    client = Client(create_app(config, docs=prefixed))
    html = client.get("/docs/desktop", base_url="https://quillsail.test").get_data(as_text=True)
    assert "<h1>Desktop launcher</h1>" in html
    assert 'href="/docs/installation"' in html
    assert 'href="/docs/static/css/docs.css' in html
    assert "Where readers write." in client.get("/", base_url="https://quillsail.test").get_data(as_text=True)


def test_addresses_must_be_distinct():
    with pytest.raises(ConfigError):
        Config.from_env({"QUILLSAIL_SITE_URL": "https://a.test", "QUILLSAIL_DOCS_URL": "https://a.test/"})
    with pytest.raises(ConfigError):
        Config.from_env({"QUILLSAIL_SITE_URL": "ftp://a.test"})


def test_proxy_hops_trust_forwarded_https(config, docs):
    from dataclasses import replace

    client = Client(create_app(replace(config, proxy_hops=1), docs=docs))
    response = client.get("/", base_url="http://internal:8000",
                          headers={"X-Forwarded-Host": "quillsail.test", "X-Forwarded-Proto": "https",
                                   "X-Forwarded-For": "203.0.113.9", "User-Agent": MAC_UA})
    assert response.status_code == 200
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


# --- updates host ----------------------------------------------------------------------------------


def test_update_metadata_redirects_to_the_signed_feed(updates):
    response = updates.get("/timestamp.json")
    assert response.status_code == 302
    assert response.location == "https://github.com/DoobaDigital/QuillSail/releases/download/desktop-updates/timestamp.json"
    assert updates.get("/12.targets.json").location.endswith("/desktop-updates/12.targets.json")
    archive = updates.get("/v1.10.0/QuillSail-1.10.0-linux-x86_64.tar.gz")
    assert archive.location == ("https://github.com/DoobaDigital/QuillSail/releases/download/v1.10.0/"
                                "QuillSail-1.10.0-linux-x86_64.tar.gz")


@pytest.mark.parametrize("path", [
    "/evil.json", "/1.root.json.exe", "/0.root.json", "/v1.10.0/QuillSail-1.9.0-linux-x86_64.tar.gz",
    "/v1.10.0/../../x", "/v1.10/QuillSail-1.10-macos-arm64.zip", "/v1.10.0/notes.txt",
])
def test_update_redirects_accept_only_exact_names(updates, path):
    assert updates.get(path).status_code == 404


# --- headers, caching and housekeeping -------------------------------------------------------------


def test_pages_have_a_strict_content_security_policy(site):
    response = site.get("/", headers={"User-Agent": MAC_UA})
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "form-action 'self' https://docs.quillsail.test" in policy
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    html = response.get_data(as_text=True)
    assert " style=" not in html
    # The only inline script is JSON-LD data, which browsers don't run.
    assert re.findall(r"<script(?! src)[^>]*>", html) == ['<script type="application/ld+json">']


def test_assets_are_fingerprinted_and_cached(site):
    html = page(site, "/")
    stylesheet = re.search(r'href="(/static/css/site\.css\?v=[0-9a-f]{10})"', html).group(1)
    response = site.get(stylesheet)
    assert response.status_code == 200
    assert response.cache_control.max_age == 31_536_000 and response.cache_control.immutable


def test_structured_data_describes_the_app(site):
    html = page(site, "/")
    graph = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
    data = next(node for node in graph["@graph"] if node["@type"] == "SoftwareApplication")
    assert data["@type"] == "SoftwareApplication" and data["softwareVersion"] == "2.3.4"
    assert data["offers"]["price"] == "0"


def test_robots_sitemap_and_icons(site, docs_client):
    assert "Sitemap: https://quillsail.test/sitemap.xml" in site.get("/robots.txt").get_data(as_text=True)
    sitemap = site.get("/sitemap.xml").get_data(as_text=True)
    assert "<loc>https://quillsail.test/download</loc>" in sitemap
    docs_sitemap = docs_client.get("/sitemap.xml").get_data(as_text=True)
    assert "<loc>https://docs.quillsail.test/install-script</loc>" in docs_sitemap
    assert "Allow: /" in docs_client.get("/robots.txt").get_data(as_text=True)
    assert "noindex" in docs_client.get("/search?q=install").headers["X-Robots-Tag"]
    for path in ("/favicon.ico", "/apple-touch-icon.png"):
        assert site.get(path).status_code == 200


def test_short_links(site):
    assert site.get("/docs").location == "https://docs.quillsail.test/"
    assert site.get("/changelog").location == "https://docs.quillsail.test/changelog"
    assert site.get("/github").location == "https://github.com/DoobaDigital/QuillSail"


def test_missing_pages_get_a_helpful_404(site, docs_client):
    response = site.get("/no-such-page")
    assert response.status_code == 404 and "We can't find that page" in response.get_data(as_text=True)
    response = docs_client.get("/no-such-page")
    assert response.status_code == 404 and 'action="/search"' in response.get_data(as_text=True)
