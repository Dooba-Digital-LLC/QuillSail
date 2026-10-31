import base64
import json
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from quillsail_site import create_app
from quillsail_site.config import Config
from quillsail_site.views import gmail, outlook


@pytest.fixture
def outlook_site(config, docs):
    config = replace(config, outlook_enabled=True, microsoft_client_id='00000000-1111-2222-3333-444444444444',
        microsoft_client_secret='PRIVATE_MICROSOFT_SECRET', outlook_broker_secret='synthetic-broker-secret-at-least-32-characters',
        oauth_relay_secret='synthetic-relay-secret-at-least-32-characters')
    app = create_app(config, docs=docs).apps['site']
    return app.test_client(), config


def code_request(config):
    return {'grant_type': 'authorization_code', 'client_id': config.microsoft_client_id,
            'code': 'synthetic-code', 'code_verifier': 'v' * 64,
            'redirect_uri': config.site.join('/oauth/outlook/callback')}


def grant(refresh='SYNTHETIC_REFRESH'):
    return {'access_token': 'SYNTHETIC_ACCESS', 'refresh_token': refresh,
            'scope': outlook.SCOPE, 'token_type': 'Bearer', 'expires_in': 3600,
            'id_token': 'IDENTITY_TOKEN_NOT_FORWARDED'}


def test_public_discovery_never_distributes_private_microsoft_credentials(outlook_site):
    client, config = outlook_site
    response = client.get('/.well-known/quillsail/outlook.json')
    assert response.status_code == 200
    assert response.json == {'version': 1, 'client_id': config.microsoft_client_id, 'scope': outlook.SCOPE, 'tenant': 'common',
        'relay_url': 'https://quillsail.test/oauth/outlook/callback',
        'token_url': 'https://quillsail.test/oauth/outlook/token'}
    assert config.microsoft_client_secret not in response.text
    assert config.outlook_broker_secret not in response.text
    assert 'Set-Cookie' not in response.headers
    assert response.headers['Cache-Control'] == 'public, max-age=300'
    assert config.microsoft_client_secret not in repr(config)


def test_outlook_is_disabled_until_provider_activation(site):
    for path in ('/.well-known/quillsail/outlook.json', '/oauth/outlook/callback'):
        assert site.get(path).status_code == 503
    assert site.post('/oauth/outlook/token', json={}).status_code == 503


def test_microsoft_exchange_keeps_secret_on_backend_and_issues_bound_refresh_proof(outlook_site, monkeypatch):
    client, config = outlook_site
    calls = []

    def upstream(fields, tenant):
        assert tenant == config.microsoft_tenant
        calls.append(fields)
        return 200, grant()

    monkeypatch.setattr(outlook, 'microsoft_request', upstream)
    response = client.post('/oauth/outlook/token', json=code_request(config))
    assert response.status_code == 200
    assert calls[0]['client_secret'] == config.microsoft_client_secret
    assert calls[0]['scope'] == outlook.SCOPE
    assert calls[0]['code_verifier'] == 'v' * 64
    assert calls[0]['redirect_uri'] == 'https://quillsail.test/oauth/outlook/callback'
    assert response.json['refresh_token'] == 'SYNTHETIC_REFRESH'
    assert outlook.valid_proof(config, 'SYNTHETIC_REFRESH', response.json['refresh_proof'])
    assert not outlook.valid_proof(config, 'ANOTHER_REFRESH', response.json['refresh_proof'])
    assert 'id_token' not in response.json
    assert config.microsoft_client_secret not in response.text and config.outlook_broker_secret not in response.text
    assert 'no-store' in response.headers['Cache-Control'] and response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'Set-Cookie' not in response.headers and 'Access-Control-Allow-Origin' not in response.headers


def test_refresh_requires_proof_and_rebinds_it_to_rotated_tokens(outlook_site, monkeypatch):
    client, config = outlook_site
    calls = []
    monkeypatch.setattr(outlook, 'microsoft_request', lambda fields, tenant: (calls.append(fields) or (200, grant('ROTATED_REFRESH'))))
    request = {'grant_type': 'refresh_token', 'client_id': config.microsoft_client_id,
               'refresh_token': 'OLD_REFRESH', 'refresh_proof': outlook.proof(config, 'OLD_REFRESH')}
    response = client.post('/oauth/outlook/token', json=request)
    assert response.status_code == 200
    assert calls[0] == {'grant_type': 'refresh_token', 'client_id': config.microsoft_client_id,
                        'client_secret': config.microsoft_client_secret, 'refresh_token': 'OLD_REFRESH', 'scope': outlook.SCOPE}
    assert outlook.valid_proof(config, 'ROTATED_REFRESH', response.json['refresh_proof'])
    assert not outlook.valid_proof(config, 'OLD_REFRESH', response.json['refresh_proof'])
    request['refresh_token'] = 'STOLEN_LEGACY_REFRESH'
    assert client.post('/oauth/outlook/token', json=request).status_code == 400
    assert len(calls) == 1


@pytest.mark.parametrize('overrides', [
    {'grant_type': 'client_credentials'}, {'client_id': 'another-client'}, {'code_verifier': 'short'},
    {'redirect_uri': 'https://attacker.example.test/callback'}, {'client_secret': 'INJECTED_SECRET'},
    {'token_endpoint': 'http://127.0.0.1/private'}, {'code': ''}, {'code': 'a' * 8193},
    {'tenant': 'attacker.example.test'}, {'scope': 'https://graph.microsoft.com/Mail.Read'},
])
def test_rejects_unsupported_grants_credentials_and_destinations_without_contacting_microsoft(outlook_site, monkeypatch, overrides):
    client, config = outlook_site

    def forbidden(fields, tenant):
        raise AssertionError('Invalid broker requests must not contact Microsoft.')

    monkeypatch.setattr(outlook, 'microsoft_request', forbidden)
    assert client.post('/oauth/outlook/token', json=code_request(config) | overrides).status_code == 400


def test_broker_rejects_browser_forms_origin_and_large_bodies(outlook_site):
    client, config = outlook_site
    path = '/oauth/outlook/token'
    assert client.post(path, data=code_request(config)).status_code == 400
    assert client.post(path, json=code_request(config), headers={'Origin': 'https://attacker.example.test'}).status_code == 400
    assert client.post(path, data=' ' * 17000, content_type='application/json').status_code == 413


@pytest.mark.parametrize('scope', [outlook.SCOPE + ' https://graph.microsoft.com/Mail.Read', 'openid email'])
def test_broker_never_returns_broader_or_missing_send_permissions(outlook_site, monkeypatch, scope):
    client, config = outlook_site
    monkeypatch.setattr(outlook, 'microsoft_request', lambda fields, tenant: (200, grant() | {'scope': scope}))
    response = client.post('/oauth/outlook/token', json=code_request(config))
    assert response.status_code == 400 and response.json == {'error': 'invalid_scope'}
    assert 'SYNTHETIC_ACCESS' not in response.text and 'SYNTHETIC_REFRESH' not in response.text


def test_provider_errors_and_bad_responses_do_not_leak_credentials(outlook_site, monkeypatch):
    client, config = outlook_site
    monkeypatch.setattr(outlook, 'microsoft_request', lambda fields, tenant: (400, {'error': 'invalid_grant',
        'error_description': 'PRIVATE_MICROSOFT_SECRET SYNTHETIC_REFRESH'}))
    response = client.post('/oauth/outlook/token', json=code_request(config))
    assert response.status_code == 400 and response.json == {'error': 'invalid_grant'}
    monkeypatch.setattr(outlook, 'microsoft_request', lambda fields, tenant: (200, {'unexpected': 'PRIVATE_MICROSOFT_SECRET'}))
    response = client.post('/oauth/outlook/token', json=code_request(config))
    assert response.status_code == 503 and response.json == {'error': 'temporarily_unavailable'}


def test_valid_requests_have_a_bounded_per_address_rate(outlook_site, monkeypatch):
    client, config = outlook_site
    monkeypatch.setattr(outlook, 'microsoft_request', lambda fields, tenant: (200, grant()))
    for _ in range(60):
        assert client.post('/oauth/outlook/token', json=code_request(config)).status_code == 200
    assert client.post('/oauth/outlook/token', json=code_request(config)).status_code == 429


def test_outlook_confirmation_is_browser_bound_and_cannot_cross_provider_routes(outlook_site):
    client, config = outlook_site
    client.application.config['QUILLSAIL'] = replace(config, gmail_enabled=True,
        google_client_id='synthetic.apps.googleusercontent.com', google_client_secret='PRIVATE_GOOGLE_SECRET',
        gmail_broker_secret='synthetic-google-broker-at-least-32-characters')
    target = 'http://127.0.0.1:5123/dashboard/settings/email/callback/outlook'
    state = base64.urlsafe_b64encode(json.dumps({'destination': target, 'nonce': 'n' * 43}).encode()).decode().rstrip('=')
    path = '/oauth/outlook/callback'
    response = client.get(path, query_string={'code': 'synthetic-code', 'state': state})
    assert response.status_code == 200 and 'Microsoft has approved' in response.text
    assert 'form-action \'self\' http://127.0.0.1:5123' in response.headers['Content-Security-Policy']
    fields = dict(re.findall(r'name="([^\"]+)" value="([^\"]+)"', response.text))
    assert client.post(path, data={'transfer': fields['transfer']}).status_code == 400
    assert client.post('/oauth/cloudflare/callback', data=fields).status_code == 400
    google_state = base64.urlsafe_b64encode(json.dumps({'destination': target.replace('/outlook', '/gmail'),
        'nonce': 'g' * 43}).encode()).decode().rstrip('=')
    google_response = client.get('/oauth/gmail/callback', query_string={'code': 'synthetic-google-code', 'state': google_state})
    google_fields = dict(re.findall(r'name="([^\"]+)" value="([^\"]+)"', google_response.text))
    assert client.post('/oauth/gmail/callback', data=fields).status_code == 400
    # Even a valid Gmail CSRF cannot authenticate an Outlook-signed transfer.
    assert client.post('/oauth/gmail/callback', data=fields | {'csrf': google_fields['csrf']}).status_code == 400
    assert client.post('/oauth/gmail/callback', data=google_fields).status_code == 303
    response = client.post(path, data=fields)
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.location).query)['code'] == ['synthetic-code']
    assert client.post(path, data=fields).status_code == 400


def test_outlook_proofs_bind_tenant_registration_and_provider(outlook_site):
    _, config = outlook_site
    signed = outlook.proof(config, 'SYNTHETIC_REFRESH')
    assert not outlook.valid_proof(replace(config, microsoft_tenant='organizations'), 'SYNTHETIC_REFRESH', signed)
    assert not outlook.valid_proof(replace(config, microsoft_client_id='different'), 'SYNTHETIC_REFRESH', signed)
    # Even reusing a deployment key must not permit cross-provider renewal.
    google = replace(config, gmail_broker_secret=config.outlook_broker_secret,
        google_client_id=config.microsoft_client_id)
    assert not outlook.valid_proof(config, 'SYNTHETIC_REFRESH', gmail.proof(google, 'SYNTHETIC_REFRESH'))


@pytest.mark.parametrize('scope', ['Mail.Send User.Read', 'email openid profile https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read'])
def test_microsoft_scope_aliases_only_allow_sending_and_identity(outlook_site, monkeypatch, scope):
    client, config = outlook_site
    monkeypatch.setattr(outlook, 'microsoft_request', lambda fields, tenant: (200, grant() | {'scope': scope}))
    assert client.post('/oauth/outlook/token', json=code_request(config)).status_code == 200


@pytest.mark.parametrize('tenant', ['../common', 'common?x=secret', 'login.attacker.test', 'common/extra'])
def test_invalid_backend_authority_never_contacts_microsoft(outlook_site, monkeypatch, tenant):
    client, config = outlook_site
    client.application.config['QUILLSAIL'] = replace(config, microsoft_tenant=tenant)
    monkeypatch.setattr(outlook, 'microsoft_request', lambda *args: pytest.fail('Unsafe authority contacted'))
    assert client.post('/oauth/outlook/token', json=code_request(config)).status_code == 503


def test_fixed_microsoft_transport_verifies_tls_and_never_follows_redirects(monkeypatch):
    calls = []

    class Response:
        status = 200

        def read(self, maximum):
            assert maximum == outlook.MAX_BYTES + 1
            return json.dumps(grant()).encode()

        def release_conn(self):
            calls.append('released')

    class Pool:
        def __init__(self, **kwargs):
            assert kwargs['cert_reqs'] == 'CERT_REQUIRED' and kwargs['ca_certs']
            assert kwargs['retries'] is False

        def request(self, method, url, **kwargs):
            assert method == 'POST' and url == 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
            assert kwargs['redirect'] is False and kwargs['preload_content'] is False
            assert kwargs['encode_multipart'] is False
            assert kwargs['fields']['client_secret'] == 'PRIVATE_MICROSOFT_SECRET'
            return Response()

        def clear(self):
            calls.append('cleared')

    monkeypatch.setattr(outlook.urllib3, 'PoolManager', Pool)
    assert outlook.microsoft_request({'client_secret': 'PRIVATE_MICROSOFT_SECRET'}, 'common')[0] == 200
    assert calls == ['released', 'cleared']


def test_private_environment_config_preserves_secrecy_and_requires_explicit_activation():
    config = Config.from_env({'QUILLSAIL_MICROSOFT_CLIENT_ID': '00000000-1111-2222-3333-444444444444',
        'QUILLSAIL_MICROSOFT_CLIENT_SECRET': 'PRIVATE_MICROSOFT_SECRET',
        'QUILLSAIL_OUTLOOK_BROKER_SECRET': 'PRIVATE_BROKER_KEY_AT_LEAST_32_CHARACTERS'})
    assert not config.outlook_enabled
    assert config.outlook_broker_secret == 'PRIVATE_BROKER_KEY_AT_LEAST_32_CHARACTERS'
    assert config.microsoft_client_secret == 'PRIVATE_MICROSOFT_SECRET'
    assert 'PRIVATE_MICROSOFT_SECRET' not in repr(config) and 'PRIVATE_BROKER_KEY' not in repr(config)
