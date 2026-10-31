"""Synthetic shared Outlook sign-in; no real Microsoft authorization or email."""
import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import responses
from sqlalchemy import select

from copydesk import email_connections as ec
from copydesk.db import db
from copydesk.models import EmailConnection, EmailOAuthFlow

METADATA = 'https://quillsail.blog/.well-known/quillsail/outlook.json'
TOKEN = 'https://quillsail.blog/oauth/outlook/token'
CLIENT = {'version': 1, 'client_id': '00000000-1111-2222-3333-444444444444',
    'scope': ec.PROVIDERS['outlook']['scope'], 'tenant': 'common', 'relay_url': 'https://quillsail.blog/oauth/outlook/callback',
    'token_url': TOKEN}
GRANT = {'access_token': 'SYNTHETIC_ACCESS', 'refresh_token': 'SYNTHETIC_REFRESH',
    'refresh_proof': 'PRIVATE_REFRESH_PROOF', 'expires_in': 3600, 'token_type': 'Bearer',
    'scope': ec.PROVIDERS['outlook']['scope']}


def start(client):
    return client.post('/dashboard/settings/email/connect/outlook', data={'csrf_token': 'test-csrf'})


def finish(client, query):
    return client.get('/dashboard/settings/email/callback/outlook', query_string={
        'state': query['state'][0], 'code': 'synthetic-code'})


def test_fresh_instance_offers_connect_outlook_without_setup_or_network(app, editor_client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Page rendering must not fetch metadata.')

    monkeypatch.setattr(requests.Session, 'request', forbidden)
    response = editor_client.get('/dashboard/settings/email')
    assert response.status_code == 200
    assert 'Connect Outlook' in response.text and 'Set up Outlook' not in response.text
    assert 'Use your own Outlook app (optional)' in response.text
    assert 'QuillSail’s website handles sign-in and token renewal.' in response.text
    assert editor_client.post('/dashboard/settings/email/connect/outlook', data={}).status_code == 400


@pytest.mark.parametrize('desktop', [False, True])
@responses.activate
def test_shared_pkce_exchange_and_encrypted_per_instance_tokens(app, editor_client, desktop):
    app.extensions['copydesk'].config.base_url = 'https://public.example.test'
    if desktop:
        app.extensions['copydesk_desktop'] = SimpleNamespace(url='http://127.0.0.1:5188')
    responses.get(METADATA, json=CLIENT)
    response = start(editor_client)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query['redirect_uri'] == [CLIENT['relay_url']]
    assert query['client_id'] == [CLIENT['client_id']]
    assert query['scope'] == [ec.PROVIDERS['outlook']['scope']]
    assert urlsplit(response.location).hostname == 'login.microsoftonline.com'
    assert urlsplit(response.location).path == '/common/oauth2/v2.0/authorize'
    assert query['code_challenge_method'] == ['S256'] and query['prompt'] == ['select_account']
    sent = query['state'][0]
    state = json.loads(base64.urlsafe_b64decode(sent + '=' * (-len(sent) % 4)))
    assert state['destination'].startswith('http://127.0.0.1:5188/' if desktop else 'https://public.example.test/')
    assert state['destination'].endswith('/dashboard/settings/email/callback/outlook')
    assert 'client_secret' not in query and 'code_verifier' not in query
    # Callback must use its saved registration, without re-fetching changed metadata.
    responses.replace(responses.GET, METADATA, status=503)
    responses.post(TOKEN, json=GRANT)
    responses.get(ec.PROVIDERS['outlook']['profile'], json={'mail': 'sender@example.test'})
    assert finish(editor_client, query).status_code == 302
    exchange = json.loads(responses.calls[1].request.body)
    assert 'client_secret' not in exchange
    assert exchange['redirect_uri'] == CLIENT['relay_url']
    assert base64.urlsafe_b64encode(hashlib.sha256(exchange['code_verifier'].encode()).digest()).decode().rstrip('=') == query['code_challenge'][0]
    with app.app_context():
        row, data = ec.connection_data()
        assert data['token']['refresh_proof'] == GRANT['refresh_proof']
        assert 'client_secret' not in data['client']
        assert all(secret not in row.payload for secret in ('SYNTHETIC_ACCESS', 'SYNTHETIC_REFRESH', 'PRIVATE_REFRESH_PROOF'))
        assert db.session.scalar(select(EmailOAuthFlow)) is None
        assert ec.effective_mail_config().from_email == 'sender@example.test'
    assert finish(editor_client, query).status_code == 400
    assert len(responses.calls) == 3


@responses.activate
def test_refresh_uses_saved_broker_and_proof_without_changing_sender(app):
    with app.app_context():
        ec.save_connection({'provider': 'outlook', 'email': 'sender@example.test',
            'token': GRANT | {'expires_at': 0}, 'client': CLIENT | {'shared': True}})
        db.session.commit()
        config = ec.effective_mail_config()
        responses.post(TOKEN, json=GRANT | {'access_token': 'FRESH_ACCESS', 'refresh_token': 'ROTATED_REFRESH', 'refresh_proof': 'ROTATED_PROOF'})
        assert ec._access_token(config) == 'FRESH_ACCESS'
        sent = json.loads(responses.calls[0].request.body)
        assert sent == {'grant_type': 'refresh_token', 'client_id': CLIENT['client_id'],
            'refresh_token': GRANT['refresh_token'], 'refresh_proof': GRANT['refresh_proof']}
        row, data = ec.connection_data()
        assert row.connection_id == config.connection_id
        assert data['token']['refresh_token'] == 'ROTATED_REFRESH'
        assert data['token']['refresh_proof'] == 'ROTATED_PROOF'
        assert 'ROTATED_PROOF' not in row.payload


@pytest.mark.parametrize(('error', 'status', 'temporary'), [('invalid_grant', 400, False), ('invalid_proof', 400, False), ('temporarily_unavailable', 503, True)])
@responses.activate
def test_refresh_errors_preserve_connection_and_distinguish_retry(app, error, status, temporary):
    with app.app_context():
        ec.save_connection({'provider': 'outlook', 'email': 'sender@example.test',
            'token': GRANT | {'expires_at': 0}, 'client': CLIENT | {'shared': True}})
        db.session.commit()
        config = ec.effective_mail_config()
        responses.post(TOKEN, status=status, json={'error': error, 'error_description': 'SYNTHETIC_REFRESH'})
        with pytest.raises(ec.ConnectionProblem) as caught:
            ec._access_token(config)
        assert caught.value.temporary is temporary
        assert 'SYNTHETIC_REFRESH' not in str(caught.value)
        assert ec.connection_data()[0].connection_id == config.connection_id


@pytest.mark.parametrize('override', [
    {'client_secret': 'DO_NOT_DISTRIBUTE'}, {'version': True},
    {'scope': ec.PROVIDERS['outlook']['scope'] + ' https://graph.microsoft.com/Mail.Read'},
    {'token_url': 'https://attacker.example.test/oauth/outlook/token'},
    {'relay_url': 'http://quillsail.blog/oauth/outlook/callback'}, {'client_id': ''},
    {'tenant': '../attacker'}, {'tenant': 'common?x=y'}, {'tenant': 'login.attacker.test'},
])
@responses.activate
def test_unsafe_metadata_never_starts_authorization(app, editor_client, override):
    responses.get(METADATA, json=CLIENT | override)
    response = start(editor_client)
    assert response.status_code == 422 and 'SMTP' in response.text
    with app.app_context():
        assert db.session.scalar(select(EmailOAuthFlow)) is None
        assert db.session.get(EmailConnection, 1) is None


@pytest.mark.parametrize('failure', ['outage', 'redirect', 'oversize'])
@responses.activate
def test_discovery_failures_leave_smtp_and_existing_custom_options(app, editor_client, failure):
    if failure == 'outage':
        responses.get(METADATA, body=requests.ConnectionError('synthetic outage'))
    elif failure == 'redirect':
        responses.get(METADATA, status=302, headers={'Location': 'https://attacker.example.test/'})
    else:
        responses.get(METADATA, body=' ' * 17000, content_type='application/json')
    response = start(editor_client)
    assert response.status_code == 422 and 'SMTP' in response.text
    assert 'Use your own Outlook app (optional)' in response.text
    assert len(responses.calls) == 1


def test_custom_microsoft_registration_can_return_to_shared_defaults(app):
    with app.app_context():
        ec.save_client('outlook', 'custom-client', 'synthetic-custom-secret')
        db.session.commit()
        assert ec.client_config('outlook')['client_id'] == 'custom-client'
        ec.save_client('outlook', '', '')
        db.session.commit()
        assert not ec.client_config('outlook')['client_id']
        assert ec.client_ready('outlook')


@responses.activate
def test_shared_refresh_cannot_restore_a_replaced_sender(app):
    with app.app_context():
        ec.save_connection({'provider': 'outlook', 'email': 'sender@example.test',
            'token': GRANT | {'expires_at': 0}, 'client': CLIENT | {'shared': True}})
        db.session.commit()
        config = ec.effective_mail_config()

        def replace_sender(request):
            ec.save_connection({'provider': 'server'})
            db.session.commit()
            return 200, {'Content-Type': 'application/json'}, json.dumps(GRANT)

        responses.add_callback(responses.POST, TOKEN, callback=replace_sender)
        with pytest.raises(ec.ConnectionProblem, match='sender changed') as caught:
            ec._access_token(config)
        assert caught.value.temporary
        assert ec.connection_data()[1]['provider'] == 'server'
