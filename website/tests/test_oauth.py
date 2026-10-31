import base64
import json
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from quillsail_site import create_app
from quillsail_site.config import Address, Config


def state(destination='https://publication.example.test/dashboard/domain/oauth/callback'):
    return base64.urlsafe_b64encode(json.dumps({'destination': destination, 'nonce': 'n' * 43}).encode()).decode().rstrip('=')


@pytest.fixture
def relay(config, docs):
    app = create_app(replace(config, oauth_relay_secret='synthetic-relay-secret-at-least-32-characters'), docs=docs)
    return app.apps['site'].test_client()


def test_relay_requires_visible_destination_click_and_csrf(relay):
    path = '/oauth/cloudflare/callback'
    response = relay.get(path, query_string={'code': 'synthetic-code', 'state': state()})
    assert response.status_code == 200 and 'Location' not in response.headers
    assert b'https://publication.example.test/dashboard/domain/oauth/callback' in response.data
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'no-store' in response.headers['Cache-Control']
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]+)"', response.text))
    assert relay.post(path, data={'transfer': fields['transfer']}).status_code == 400
    response = relay.post(path, data=fields)
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.location).query)
    assert query['code'] == ['synthetic-code'] and query['state'] == [state()]
    assert relay.post(path, data=fields).status_code == 400


@pytest.mark.parametrize('destination', ['javascript:alert(1)', 'http://evil.example.test/dashboard/domain/oauth/callback', 'https://user:secret@example.test/dashboard/domain/oauth/callback', 'https://example.test/other', 'https://example.test/dashboard/domain/oauth/callback?override=1'])
def test_relay_rejects_unsafe_destinations(relay, destination):
    assert relay.get('/oauth/cloudflare/callback', query_string={'code': 'synthetic', 'state': state(destination)}).status_code == 400


def test_relay_allows_loopback_destination_and_remains_disabled_without_configuration(relay, site):
    path = '/oauth/cloudflare/callback'
    query = {'code': 'synthetic', 'state': state('http://127.0.0.1:5123/dashboard/domain/oauth/callback')}
    assert relay.get(path, query_string=query).status_code == 200
    assert site.get(path, query_string=query).status_code == 503


def test_relay_csp_allows_only_the_confirmed_redirect_origin(relay):
    response = relay.get('/oauth/cloudflare/callback', query_string={
        'code': 'synthetic-code', 'state': state('http://127.0.0.1:5123/dashboard/domain/oauth/callback')})
    csp = response.headers['Content-Security-Policy']
    assert "form-action 'self' http://127.0.0.1:5123" in csp
    assert '*' not in csp


def test_relay_error_responses_also_hide_query_referrers(relay):
    response = relay.get('/oauth/cloudflare/callback')
    assert response.status_code == 400
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'no-store' in response.headers['Cache-Control']


def test_shared_client_document_contains_only_public_registration(config, docs):
    config = replace(config, cloudflare_client_id='synthetic-shared-client',
                     oauth_relay_secret='synthetic-private-relay-secret-at-least-32-characters')
    client = create_app(config, docs=docs).apps['site'].test_client()
    response = client.get('/.well-known/quillsail/cloudflare.json')
    assert response.status_code == 200
    assert response.json == {'version': 1, 'client_id': 'synthetic-shared-client',
        'scopes': 'dns.write zone.read argotunnel.write offline_access', 'edge_scopes': '',
        'relay_url': 'https://quillsail.test/oauth/cloudflare/callback'}
    assert config.oauth_relay_secret not in response.text
    assert 'Set-Cookie' not in response.headers
    assert response.headers['Cache-Control'] == 'public, max-age=300'
    assert 'noindex' in response.headers['X-Robots-Tag']


@pytest.mark.parametrize('missing', ['client', 'secret', 'https'])
def test_shared_client_document_requires_complete_website_setup(config, docs, missing):
    config = replace(config, cloudflare_client_id='synthetic-shared-client',
                     oauth_relay_secret='synthetic-private-relay-secret-at-least-32-characters')
    config = replace(config, **{
        'client': {'cloudflare_client_id': ''}, 'secret': {'oauth_relay_secret': ''},
        'https': {'site': Address.parse('site', 'http://quillsail.test')},
    }[missing])
    client = create_app(config, docs=docs).apps['site'].test_client()
    assert client.get('/.well-known/quillsail/cloudflare.json').status_code == 503


def test_maintainer_configures_shared_client_once_in_website_environment():
    config = Config.from_env({'QUILLSAIL_CLOUDFLARE_CLIENT_ID': 'synthetic-shared-client',
        'QUILLSAIL_CLOUDFLARE_SCOPES': 'dns.write zone.read argotunnel.write offline_access',
        'QUILLSAIL_CLOUDFLARE_EDGE_SCOPES': 'synthetic.optional'})
    assert config.cloudflare_client_id == 'synthetic-shared-client'
    assert config.cloudflare_edge_scopes == 'synthetic.optional'
