"""Search engines receive consistent metadata and can read indexing directives."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest
from werkzeug.test import Client

from quillsail_site import create_app
from quillsail_site.config import Config
from quillsail_site.docs import Docs
from quillsail_site.web import STATIC


class Head(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.meta, self.canonicals, self.schemas = {}, [], []
        self.title, self._title, self._schema = '', False, None
        self.feed(response.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('name') or attrs.get('property')] = attrs.get('content')
        elif tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonicals.append(attrs['href'])
        elif tag == 'title':
            self._title = True
        elif tag == 'script' and attrs.get('type') == 'application/ld+json':
            self._schema = ''

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self._schema is not None:
            self._schema += data

    def handle_endtag(self, tag):
        if tag == 'title':
            self._title = False
        if tag == 'script' and self._schema is not None:
            self.schemas.append(json.loads(self._schema))
            self._schema = None


@pytest.mark.parametrize(('app', 'path', 'url'), [
    ('site', '/', 'https://quillsail.test/'),
    ('site', '/?utm_source=example', 'https://quillsail.test/'),
    ('site', '/download?utm_source=example', 'https://quillsail.test/download'),
    ('docs', '/', 'https://docs.quillsail.test/'),
    ('docs', '/install-script?utm_source=example', 'https://docs.quillsail.test/install-script'),
])
def test_public_page_metadata_agrees_with_its_canonical(website, app, path, url):
    response = website.apps[app].test_client().get(path)
    assert response.status_code == 200
    head = Head(response)
    assert head.canonicals == [url]
    assert head.meta['og:url'] == url
    assert head.title == head.meta['og:title'] == head.meta['twitter:title']
    assert head.meta['description'] == head.meta['og:description'] == head.meta['twitter:description']
    assert 50 <= len(head.meta['description']) <= 160
    assert head.meta['og:image'] == head.meta['twitter:image']
    assert urlsplit(head.meta['og:image']).scheme == 'https'
    assert head.meta['twitter:image:alt']
    assert 'max-image-preview:large' in head.meta['robots']
    assert 'noindex' not in response.headers.get('X-Robots-Tag', '')
    assert len(head.schemas) == 1
    graph = head.schemas[0]['@graph']
    webpage = next(node for node in graph if node['@id'] == url + '#webpage')
    assert webpage['url'] == url and webpage['name'] == head.title
    assert webpage['description'] == head.meta['description']


def test_software_and_site_identity_use_factual_release_data(site):
    head = Head(site.get('/'))
    graph = {node['@type']: node for node in head.schemas[0]['@graph']}
    assert graph['WebSite']['name'] == 'QuillSail'
    assert graph['WebSite']['url'] == 'https://quillsail.test/'
    app = graph['SoftwareApplication']
    assert app['softwareVersion'] == '2.3.4'
    assert app['offers']['price'] == '0' and app['isAccessibleForFree']
    assert app['license'] == 'https://opensource.org/licenses/MIT'
    assert set(app['operatingSystem']) == {'macOS', 'Windows', 'Linux'}
    assert 'aggregateRating' not in app and 'review' not in app


def test_release_failure_does_not_invent_a_software_version(config, docs, fixture_file):
    fixture_file.write_text(json.dumps({'channels': {}}))
    client = create_app(config, docs=docs).apps['site'].test_client()
    graph = Head(client.get('/')).schemas[0]['@graph']
    app = next(node for node in graph if node['@type'] == 'SoftwareApplication')
    assert 'softwareVersion' not in app and 'releaseNotes' not in app
    assert Head(client.get('/')).canonicals == ['https://quillsail.test/']


@pytest.mark.parametrize(('app', 'path', 'labels'), [
    ('site', '/download', ['Home', 'Download']),
    ('docs', '/', ['QuillSail', 'Documentation']),
    ('docs', '/install-script', ['QuillSail', 'Documentation', 'Terminal installer']),
])
def test_breadcrumbs_are_visible_and_match_structured_data(website, app, path, labels):
    response = website.apps[app].test_client().get(path)
    assert 'aria-label="Breadcrumb"' in response.get_data(as_text=True)
    head = Head(response)
    graph = head.schemas[0]['@graph']
    trail = next(node for node in graph if node['@type'] == 'BreadcrumbList')['itemListElement']
    assert [item['name'] for item in trail] == labels
    assert [item['position'] for item in trail] == list(range(1, len(labels) + 1))
    assert trail[-1]['item'] == head.canonicals[0]
    if path == '/install-script':
        article = next(node for node in graph if node['@type'] == 'TechArticle')
        assert article['headline'] == 'Terminal installer'
        assert article['mainEntityOfPage']['@id'] == head.canonicals[0] + '#webpage'


@pytest.mark.parametrize(('app', 'path'), [
    ('site', '/healthz'), ('site', '/api/v1/releases/latest'),
    ('site', '/api/v1/releases/latest/linux-x86_64.txt'), ('site', '/install.sh'),
    ('site', '/download/linux-x86_64'), ('site', '/download?started=linux-x86_64'),
    ('site', '/download?started=not-a-platform'), ('site', '/no-such-page'),
    ('docs', '/search?q=installation'), ('docs', '/search.json?q=installation'),
    ('docs', '/no-such-page'), ('updates', '/'), ('updates', '/timestamp.json'),
    ('updates', '/healthz'),
])
def test_utility_and_error_urls_are_not_indexable(website, app, path):
    response = website.apps[app].test_client().get(path)
    assert 'noindex' in response.headers['X-Robots-Tag']
    if response.mimetype == 'text/html' and response.status_code == 200:
        assert Head(response).meta['robots'] == 'noindex'


def test_search_can_be_crawled_to_observe_noindex(docs_client):
    robots = docs_client.get('/robots.txt').get_data(as_text=True)
    assert 'Disallow: /search' not in robots
    assert Head(docs_client.get('/search?q=blog')).meta['robots'] == 'noindex'


@pytest.mark.parametrize(('host', 'path'), [
    ('quillsail.test', '/'), ('quillsail.test', '/download?utm_source=test'),
    ('docs.quillsail.test', '/installation'), ('updates.quillsail.test', '/timestamp.json'),
])
def test_http_redirects_permanently_to_configured_https(website, host, path):
    response = Client(website).get(path, base_url='http://' + host)
    assert response.status_code == 308
    assert response.location == 'https://' + host + path
    health = Client(website).get('/healthz', base_url='http://' + host)
    assert health.status_code == (404 if host.startswith('docs.') else 200)


def test_font_preload_matches_the_stylesheet_and_uses_immutable_caching(site):
    html = site.get('/').get_data(as_text=True)
    font = re.search(r'href="([^"]+)" as="font"', html).group(1)
    css = (STATIC / 'css/site.css').read_text()
    assert '../fonts/' + font.split('/fonts/')[1] in css
    response = site.get(font)
    assert response.status_code == 200
    assert response.cache_control.immutable
    assert response.cache_control.max_age == 31_536_000


def test_html_disallows_proxy_script_injection_and_keeps_page_caching(site):
    response = site.get('/')
    assert response.cache_control.no_transform
    assert response.cache_control.public
    assert response.cache_control.max_age == 300
    assert "script-src 'self'" in response.headers['Content-Security-Policy']


def test_metadata_supports_docs_under_a_path(fixture_file, docs):
    config = Config.from_env({'QUILLSAIL_SITE_URL': 'https://quillsail.test',
        'QUILLSAIL_DOCS_URL': 'https://quillsail.test/docs', 'QUILLSAIL_UPDATES_URL': ''},
        releases_fixture=fixture_file, warm_releases=False)
    prefixed = Docs(docs.root, repository=docs.repository, prefix='/docs')
    client = Client(create_app(config, docs=prefixed))
    response = client.get('/docs/install-script', base_url='https://quillsail.test')
    head = Head(response)
    assert head.canonicals == ['https://quillsail.test/docs/install-script']
    assert head.meta['og:url'] == head.canonicals[0]
    assert client.get('/docs/install-script', base_url='http://quillsail.test').location == head.canonicals[0]
    robots = client.get('/robots.txt', base_url='https://quillsail.test').get_data(as_text=True)
    assert 'Sitemap: https://quillsail.test/docs/sitemap.xml' in robots


def test_sitemaps_contain_only_indexable_canonical_pages(website):
    titles = []
    for name in ('site', 'docs'):
        client = website.apps[name].test_client()
        tree = ElementTree.fromstring(client.get('/sitemap.xml').data)
        urls = [node.text for node in tree.findall('{*}url/{*}loc')]
        assert urls and len(urls) == len(set(urls))
        for url in urls:
            assert not urlsplit(url).query
            response = client.get(urlsplit(url).path)
            assert response.status_code == 200
            head = Head(response)
            assert head.canonicals == [url]
            titles.append(head.title)
            assert 'noindex' not in response.headers.get('X-Robots-Tag', '')
    assert len(titles) == len(set(titles))
