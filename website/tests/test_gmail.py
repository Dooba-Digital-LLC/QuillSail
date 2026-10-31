import base64
import json
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from quillsail_site import create_app
from quillsail_site.config import Config
from quillsail_site.views import gmail


@pytest.fixture
def gmail_site(config, docs):
    config = replace(config, gmail_enabled=True, google_client_id='synthetic.apps.googleusercontent.com',
        google_client_secret='PRIVATE_GOOGLE_SECRET', gmail_broker_secret='synthetic-broker-secret-at-least-32-characters',
        oauth_relay_secret='synthetic-relay-secret-at-least-32-characters')
    app = create_app(config, docs=docs).apps['site']
    return app.test_client(), config


def code_request(config):
    return {'grant_type': 'authorization_code', 'client_id': config.google_client_id,
            'code': 'synthetic-code', 'code_verifier': 'v' * 64,
            'redirect_uri': config.site.join('/oauth/gmail/callback')}


def grant(refresh='SYNTHETIC_REFRESH'):
    return {'access_token': 'SYNTHETIC_ACCESS', 'refresh_token': refresh,
            'scope': gmail.SCOPE, 'token_type': 'Bearer', 'expires_in': 3600,
            'id_token': 'IDENTITY_TOKEN_NOT_FORWARDED'}


def test_public_discovery_never_distributes_private_google_credentials(gmail_site):
    client, config = gmail_site
    response = client.get('/.well-known/quillsail/gmail.json')
    assert response.status_code == 200
    assert response.json == {'version': 1, 'client_id': config.google_client_id, 'scope': gmail.SCOPE,
        'relay_url': 'https://quillsail.test/oauth/gmail/callback',
        'token_url': 'https://quillsail.test/oauth/gmail/token'}
    assert config.google_client_secret not in response.text
    assert config.gmail_broker_secret not in response.text
    assert 'Set-Cookie' not in response.headers
    assert response.headers['Cache-Control'] == 'public, max-age=300'
    assert config.google_client_secret not in repr(config)


def test_gmail_is_disabled_until_provider_activation(site):
    for path in ('/.well-known/quillsail/gmail.json', '/oauth/gmail/callback'):
        assert site.get(path).status_code == 503
    assert site.post('/oauth/gmail/token', json={}).status_code == 503


def test_google_exchange_keeps_secret_on_backend_and_issues_bound_refresh_proof(gmail_site, monkeypatch):
    client, config = gmail_site
    calls = []

    def upstream(fields):
        calls.append(fields)
        return 200, grant()

    monkeypatch.setattr(gmail, 'google_request', upstream)
    response = client.post('/oauth/gmail/token', json=code_request(config))
    assert response.status_code == 200
    assert calls[0]['client_secret'] == config.google_client_secret
    assert calls[0]['code_verifier'] == 'v' * 64
    assert calls[0]['redirect_uri'] == 'https://quillsail.test/oauth/gmail/callback'
    assert response.json['refresh_token'] == 'SYNTHETIC_REFRESH'
    assert gmail.valid_proof(config, 'SYNTHETIC_REFRESH', response.json['refresh_proof'])
    assert not gmail.valid_proof(config, 'ANOTHER_REFRESH', response.json['refresh_proof'])
    assert 'id_token' not in response.json
    assert config.google_client_secret not in response.text and config.gmail_broker_secret not in response.text
    assert 'no-store' in response.headers['Cache-Control'] and response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'Set-Cookie' not in response.headers and 'Access-Control-Allow-Origin' not in response.headers


def test_refresh_requires_proof_and_rebinds_it_to_rotated_tokens(gmail_site, monkeypatch):
    client, config = gmail_site
    calls = []
    monkeypatch.setattr(gmail, 'google_request', lambda fields: (calls.append(fields) or (200, grant('ROTATED_REFRESH'))))
    request = {'grant_type': 'refresh_token', 'client_id': config.google_client_id,
               'refresh_token': 'OLD_REFRESH', 'refresh_proof': gmail.proof(config, 'OLD_REFRESH')}
    response = client.post('/oauth/gmail/token', json=request)
    assert response.status_code == 200
    assert calls[0] == {'grant_type': 'refresh_token', 'client_id': config.google_client_id,
                        'client_secret': config.google_client_secret, 'refresh_token': 'OLD_REFRESH'}
    assert gmail.valid_proof(config, 'ROTATED_REFRESH', response.json['refresh_proof'])
    assert not gmail.valid_proof(config, 'OLD_REFRESH', response.json['refresh_proof'])
    request['refresh_token'] = 'STOLEN_LEGACY_REFRESH'
    assert client.post('/oauth/gmail/token', json=request).status_code == 400
    assert len(calls) == 1


@pytest.mark.parametrize('overrides', [
    {'grant_type': 'client_credentials'}, {'client_id': 'another-client'}, {'code_verifier': 'short'},
    {'redirect_uri': 'https://attacker.example.test/callback'}, {'client_secret': 'INJECTED_SECRET'},
    {'token_endpoint': 'http://127.0.0.1/private'}, {'code': ''}, {'code': 'a' * 8193},
])
def test_rejects_unsupported_grants_credentials_and_destinations_without_contacting_google(gmail_site, monkeypatch, overrides):
    client, config = gmail_site

    def forbidden(fields):
        raise AssertionError('Invalid broker requests must not contact Google.')

    monkeypatch.setattr(gmail, 'google_request', forbidden)
    assert client.post('/oauth/gmail/token', json=code_request(config) | overrides).status_code == 400


def test_broker_rejects_browser_forms_origin_and_large_bodies(gmail_site):
    client, config = gmail_site
    path = '/oauth/gmail/token'
    assert client.post(path, data=code_request(config)).status_code == 400
    assert client.post(path, json=code_request(config), headers={'Origin': 'https://attacker.example.test'}).status_code == 400
    assert client.post(path, data=' ' * 17000, content_type='application/json').status_code == 413


@pytest.mark.parametrize('scope', [gmail.SCOPE + ' https://www.googleapis.com/auth/gmail.readonly', 'openid email'])
def test_broker_never_returns_broader_or_missing_send_permissions(gmail_site, monkeypatch, scope):
    client, config = gmail_site
    monkeypatch.setattr(gmail, 'google_request', lambda fields: (200, grant() | {'scope': scope}))
    response = client.post('/oauth/gmail/token', json=code_request(config))
    assert response.status_code == 400 and response.json == {'error': 'invalid_scope'}
    assert 'SYNTHETIC_ACCESS' not in response.text and 'SYNTHETIC_REFRESH' not in response.text


def test_provider_errors_and_bad_responses_do_not_leak_credentials(gmail_site, monkeypatch):
    client, config = gmail_site
    monkeypatch.setattr(gmail, 'google_request', lambda fields: (400, {'error': 'invalid_grant',
        'error_description': 'PRIVATE_GOOGLE_SECRET SYNTHETIC_REFRESH'}))
    response = client.post('/oauth/gmail/token', json=code_request(config))
    assert response.status_code == 400 and response.json == {'error': 'invalid_grant'}
    monkeypatch.setattr(gmail, 'google_request', lambda fields: (200, {'unexpected': 'PRIVATE_GOOGLE_SECRET'}))
    response = client.post('/oauth/gmail/token', json=code_request(config))
    assert response.status_code == 503 and response.json == {'error': 'temporarily_unavailable'}


def test_valid_requests_have_a_bounded_per_address_rate(gmail_site, monkeypatch):
    client, config = gmail_site
    monkeypatch.setattr(gmail, 'google_request', lambda fields: (200, grant()))
    for _ in range(60):
        assert client.post('/oauth/gmail/token', json=code_request(config)).status_code == 200
    assert client.post('/oauth/gmail/token', json=code_request(config)).status_code == 429


def test_gmail_confirmation_is_browser_bound_and_cannot_cross_provider_routes(gmail_site):
    client, _ = gmail_site
    target = 'http://127.0.0.1:5123/dashboard/settings/email/callback/gmail'
    state = base64.urlsafe_b64encode(json.dumps({'destination': target, 'nonce': 'n' * 43}).encode()).decode().rstrip('=')
    path = '/oauth/gmail/callback'
    response = client.get(path, query_string={'code': 'synthetic-code', 'state': state})
    assert response.status_code == 200 and 'Google has approved' in response.text
    assert 'form-action \'self\' http://127.0.0.1:5123' in response.headers['Content-Security-Policy']
    fields = dict(re.findall(r'name="([^\"]+)" value="([^\"]+)"', response.text))
    assert client.post(path, data={'transfer': fields['transfer']}).status_code == 400
    assert client.post('/oauth/cloudflare/callback', data=fields).status_code == 400
    response = client.post(path, data=fields)
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.location).query)['code'] == ['synthetic-code']
    assert client.post(path, data=fields).status_code == 400


def test_fixed_google_transport_verifies_tls_and_never_follows_redirects(monkeypatch):
    calls = []

    class Response:
        status = 200

        def read(self, maximum):
            assert maximum == gmail.MAX_BYTES + 1
            return json.dumps(grant()).encode()

        def release_conn(self):
            calls.append('released')

    class Pool:
        def __init__(self, **kwargs):
            assert kwargs['cert_reqs'] == 'CERT_REQUIRED' and kwargs['ca_certs']
            assert kwargs['retries'] is False

        def request(self, method, url, **kwargs):
            assert method == 'POST' and url == 'https://oauth2.googleapis.com/token'
            assert kwargs['redirect'] is False and kwargs['preload_content'] is False
            assert kwargs['encode_multipart'] is False
            assert kwargs['fields']['client_secret'] == 'PRIVATE_GOOGLE_SECRET'
            return Response()

        def clear(self):
            calls.append('cleared')

    monkeypatch.setattr(gmail.urllib3, 'PoolManager', Pool)
    assert gmail.google_request({'client_secret': 'PRIVATE_GOOGLE_SECRET'})[0] == 200
    assert calls == ['released', 'cleared']


def test_private_environment_config_preserves_secrecy_and_requires_explicit_activation():
    config = Config.from_env({'QUILLSAIL_GOOGLE_CLIENT_ID': 'synthetic.apps.googleusercontent.com',
        'QUILLSAIL_GOOGLE_CLIENT_SECRET': 'PRIVATE_GOOGLE_SECRET',
        'QUILLSAIL_GMAIL_BROKER_SECRET': 'PRIVATE_BROKER_KEY_AT_LEAST_32_CHARACTERS'})
    assert not config.gmail_enabled
    assert config.google_client_secret == 'PRIVATE_GOOGLE_SECRET'
    assert 'PRIVATE_GOOGLE_SECRET' not in repr(config) and 'PRIVATE_BROKER_KEY' not in repr(config)


def test_privacy_policy_requires_operator_review_and_public_contact(config, docs):
    client = create_app(config, docs=docs).apps['site'].test_client()
    assert client.get('/privacy').status_code == 404
    approved = replace(config, privacy_contact='privacy@example.test')
    response = create_app(approved, docs=docs).apps['site'].test_client().get('/privacy')
    assert response.status_code == 200
    assert 'privacy@example.test' in response.text and 'Limited Use' in response.text
    assert 'does not request permission to read your inbox' in response.text
    home = create_app(approved, docs=docs).apps['site'].test_client().get('/')
    assert 'href="/privacy"' in home.text
