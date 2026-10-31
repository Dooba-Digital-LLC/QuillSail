"""Shared public OAuth metadata; credentials remain on each publication."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import responses
from sqlalchemy import select

from copydesk import cloudflare, hosting_oauth, settings
from copydesk.db import db
from copydesk.models import HostingOAuthFlow

METADATA = 'https://quillsail.blog/.well-known/quillsail/cloudflare.json'
CLIENT = {'version': 1, 'client_id': 'synthetic-shared-client',
          'relay_url': 'https://relay.example.test/oauth/cloudflare/callback',
          'scopes': hosting_oauth.DEFAULT_SCOPES, 'edge_scopes': ''}


def test_fresh_publication_shows_sign_in_without_network_or_client_setup(app, editor_client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Rendering must not contact the shared service.')

    monkeypatch.setattr(requests.Session, 'request', forbidden)
    response = editor_client.get('/dashboard/domain')
    assert response.status_code == 200
    assert 'Sign in with Cloudflare' in response.text
    assert 'Use an API token instead (backup)' in response.text
    assert '<details><summary>Advanced: use your own Cloudflare OAuth app</summary>' in response.text
    assert editor_client.post('/dashboard/domain', data={'action': 'oauth'}).status_code == 400


@pytest.mark.parametrize('desktop', [False, True])
@responses.activate
def test_discovery_starts_pkce_and_callback_uses_its_original_client(app, editor_client, desktop):
    app.extensions['copydesk'].config.base_url = 'https://publication.example.test'
    if desktop:
        app.extensions['copydesk_desktop'] = SimpleNamespace(url='http://127.0.0.1:5164')
    responses.get(METADATA, json=CLIENT)
    response = editor_client.post('/dashboard/domain', data={'action': 'oauth', 'csrf_token': 'test-csrf'})
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query['client_id'] == [CLIENT['client_id']]
    assert query['redirect_uri'] == [CLIENT['relay_url']]
    assert query['code_challenge_method'] == ['S256']
    assert 'client_secret' not in query and 'code_verifier' not in query
    saved_state = query['state'][0]
    # A developer changing the hosted document cannot change an in-flight flow.
    responses.replace(responses.GET, METADATA, status=503)
    responses.post(hosting_oauth.TOKEN, json={'access_token': 'synthetic-access',
        'refresh_token': 'synthetic-refresh', 'expires_in': 3600})
    response = editor_client.get('/dashboard/domain/oauth/callback', query_string={
        'state': saved_state, 'code': 'synthetic-code'})
    assert response.status_code == 302
    assert len(responses.calls) == 2
    exchange = parse_qs(responses.calls[1].request.body)
    assert exchange['client_id'] == [CLIENT['client_id']]
    assert 'code_verifier' in exchange and 'client_secret' not in exchange
    with app.app_context():
        row, credentials = cloudflare.connection()
        assert credentials['client']['client_id'] == CLIENT['client_id']
        assert 'synthetic-access' not in row.payload and 'synthetic-refresh' not in row.payload
        assert settings.get('hosting.oauth_client') is None
        assert db.session.scalar(select(HostingOAuthFlow)) is None


@pytest.mark.parametrize('overrides', [
    {'version': 2}, {'version': True}, {'client_id': ''}, {'scopes': 'dns.write'},
    {'relay_url': 'http://relay.example.test/oauth/cloudflare/callback'},
    {'relay_url': 'https://user:password@relay.example.test/oauth/cloudflare/callback'},
    {'relay_url': 'https://relay.example.test/other'}, {'client_secret': 'synthetic-secret'},
])
@responses.activate
def test_invalid_or_sensitive_shared_metadata_is_rejected(app, editor_client, overrides):
    responses.get(METADATA, json={**CLIENT, **overrides})
    response = editor_client.post('/dashboard/domain', data={'action': 'oauth', 'csrf_token': 'test-csrf'})
    assert response.status_code == 422
    assert 'temporarily unavailable' in response.text
    assert 'API-token backup' in response.text
    assert len(responses.calls) == 1
    with app.app_context():
        assert db.session.scalar(select(HostingOAuthFlow)) is None


@pytest.mark.parametrize('failure', ['outage', 'redirect', 'oversize', 'wrong-type'])
@responses.activate
def test_shared_discovery_fails_closed_and_retains_backup(app, editor_client, failure):
    if failure == 'outage':
        responses.get(METADATA, body=requests.ConnectionError('synthetic outage'))
    elif failure == 'redirect':
        responses.get(METADATA, status=302, headers={'Location': 'https://other.example.test/'})
    elif failure == 'oversize':
        responses.get(METADATA, body=' ' * 17000, content_type='application/json')
    else:
        responses.get(METADATA, body='not json', content_type='text/html')
    response = editor_client.post('/dashboard/domain', data={'action': 'oauth', 'csrf_token': 'test-csrf'})
    assert response.status_code == 422
    assert 'Use an API token instead (backup)' in response.text
    assert len(responses.calls) == 1


@responses.activate
def test_custom_client_takes_precedence_and_can_be_cleared(app, editor_client):
    with app.app_context():
        hosting_oauth.configure('synthetic-custom-client', 'https://custom.example.test/oauth/cloudflare/callback')
        db.session.commit()
    app.extensions['copydesk'].config.base_url = 'https://publication.example.test'
    response = editor_client.post('/dashboard/domain', data={'action': 'oauth', 'csrf_token': 'test-csrf'})
    assert parse_qs(urlsplit(response.location).query)['client_id'] == ['synthetic-custom-client']
    assert not responses.calls
    response = editor_client.post('/dashboard/domain', data={'action': 'oauth.setup', 'csrf_token': 'test-csrf',
        'client_id': '', 'relay_url': ''})
    assert response.status_code == 302
    responses.get(METADATA, json=CLIENT)
    response = editor_client.post('/dashboard/domain', data={'action': 'oauth', 'csrf_token': 'test-csrf'})
    assert parse_qs(urlsplit(response.location).query)['client_id'] == [CLIENT['client_id']]


@responses.activate
def test_explicitly_disabled_discovery_does_not_make_requests(app, editor_client):
    app.extensions['copydesk'].config.oauth_clients['cloudflare']['metadata_url'] = ''
    response = editor_client.post('/dashboard/domain', data={'action': 'oauth', 'csrf_token': 'test-csrf'})
    assert response.status_code == 422 and 'sign-in is disabled' in response.text
    assert not responses.calls
