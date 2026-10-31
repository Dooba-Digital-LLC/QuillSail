"""Cloudflare public-client PKCE with a consent relay and browser-bound state."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import InvalidToken
from flask import current_app, session, url_for
from sqlalchemy import delete, update

from copydesk import cloudflare, domains, jobs, settings
from copydesk.db import db
from copydesk.helpers import state
from copydesk.models import HostingConnection, HostingOAuthFlow
from copydesk.utils import utcnow

AUTHORIZE = 'https://dash.cloudflare.com/oauth2/auth'
TOKEN = 'https://dash.cloudflare.com/oauth2/token'
REVOKE = 'https://dash.cloudflare.com/oauth2/revoke'
TTL = timedelta(minutes=10)
DEFAULT_SCOPES = 'dns.write zone.read argotunnel.write offline_access'


def config():
    # Public configuration only. The relay never needs a client secret.
    client = dict(state().config.oauth_clients.get('cloudflare', {}))
    client.update(settings.get('hosting.oauth_client', {}))
    client['scopes'] = client.get('scopes') or DEFAULT_SCOPES
    client['edge_scopes'] = client.get('edge_scopes', '')
    return client


def configure(client_id, relay_url):
    import re

    client_id = str(client_id or '').strip()
    relay_url = str(relay_url or '').strip()
    parts = urlsplit(relay_url)
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', client_id):
        raise domains.DomainProblem('Enter the registered public Cloudflare client ID.')
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or len(relay_url) > 2048
            or not parts.path.endswith('/oauth/cloudflare/callback')):
        raise domains.DomainProblem('Use the registered HTTPS OAuth relay callback URL.')
    if db.session.get(HostingConnection, 1):
        raise domains.DomainProblem('Disconnect the current hosting connection before changing the OAuth app.')
    settings.set_value('hosting.oauth_client', {'client_id': client_id, 'relay_url': relay_url,
        'scopes': DEFAULT_SCOPES})


def available():
    client = config()
    return bool(client.get('client_id') and client.get('scopes') and client.get('relay_url'))


def start(user, *, for_edge=False, reconnect=False):
    client = config()
    if not available():
        raise domains.DomainProblem('Configure the registered Cloudflare client ID, scopes and relay URL first.')
    relay = urlsplit(client['relay_url'])
    if relay.scheme != 'https' or not relay.hostname or relay.username or relay.query or relay.fragment:
        raise domains.DomainProblem('The OAuth relay must use an HTTPS callback URL.')
    existing = db.session.get(HostingConnection, 1)
    if existing and not (for_edge or reconnect):
        raise domains.DomainProblem('Disconnect the current hosting connection before signing in.')
    if for_edge and (not existing or not client.get('edge_scopes')):
        raise domains.DomainProblem('Configure optional offline-fallback OAuth scopes before granting them.')
    runtime = current_app.extensions.get('copydesk_desktop')
    destination = (runtime.url if runtime else state().config.origin) + url_for('dash.domain_oauth_callback')
    if not runtime and urlsplit(destination).scheme != 'https':
        raise domains.DomainProblem('Use HTTPS before connecting Cloudflare.')
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    relay_state = base64.urlsafe_b64encode(json.dumps({'destination': destination, 'nonce': nonce}, separators=(',', ':')).encode()).decode().rstrip('=')
    flow = HostingOAuthFlow(state_hash=hashlib.sha256(relay_state.encode()).hexdigest(), user_id=user.id,
        auth_version=user.auth_version, payload=cloudflare.encrypt({'verifier': verifier, 'client': client, 'for_edge': for_edge, 'reconnect': reconnect, 'connection_version': existing.version if existing else None}))
    db.session.execute(delete(HostingOAuthFlow).where(HostingOAuthFlow.created_at < utcnow() - TTL))
    db.session.add(flow)
    session['_hosting_oauth_state'] = relay_state
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return AUTHORIZE + '?' + urlencode({'client_id': client['client_id'], 'redirect_uri': client['relay_url'],
        'response_type': 'code', 'scope': ' '.join(dict.fromkeys((client['scopes'] + ' offline_access' + (' ' + client['edge_scopes'] if for_edge else '')).split())), 'state': relay_state,
        'code_challenge': challenge, 'code_challenge_method': 'S256'})


def _request(data):
    try:
        with requests.Session() as http:
            http.trust_env = False
            response = http.post(TOKEN, data=data, timeout=(3, 15), allow_redirects=False)
        token = response.json()
        if not response.ok or not token.get('access_token'):
            raise ValueError
        return token
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        raise domains.DomainProblem('Cloudflare authorization could not be completed. Start sign-in again.') from None


def finish(user, sent_state, code):
    expected = session.get('_hosting_oauth_state', '')
    if not sent_state or not expected or len(sent_state) > 4096 or not hmac.compare_digest(expected, sent_state):
        raise domains.DomainProblem('This sign-in did not start in this browser. Start it again.')
    digest = hashlib.sha256(sent_state.encode()).hexdigest()
    flow = db.session.get(HostingOAuthFlow, digest)
    if not flow or flow.user_id != user.id or flow.auth_version != user.auth_version or flow.created_at < utcnow() - TTL:
        raise domains.DomainProblem('This sign-in has expired. Start it again.')
    try:
        payload = json.loads(cloudflare._cipher().decrypt(flow.payload.encode()))
    except (InvalidToken, ValueError):
        raise domains.DomainProblem('Saved authorization could not be opened. Start sign-in again.') from None
    if payload['client'] != config() or not code or len(code) > 8192:
        raise domains.DomainProblem('The client setup changed or authorization was cancelled. Start again.')
    result = db.session.execute(delete(HostingOAuthFlow).where(HostingOAuthFlow.state_hash == digest))
    db.session.commit()
    session.pop('_hosting_oauth_state', None)
    if result.rowcount != 1:
        raise domains.DomainProblem('This sign-in has already been used.')
    existing = db.session.get(HostingConnection, 1)
    if (existing.version if existing else None) != payload.get('connection_version'):
        raise domains.DomainProblem('The hosting connection changed during sign-in. Start again.')
    token = _request({'grant_type': 'authorization_code', 'client_id': payload['client']['client_id'],
        'redirect_uri': payload['client']['relay_url'], 'code': code, 'code_verifier': payload['verifier']})
    if not token.get('refresh_token'):
        raise domains.DomainProblem('Cloudflare did not grant background access. Reconnect with offline access enabled for the client.')
    token['expires_at'] = time.time() + int(token.get('expires_in', 300))
    if payload.get('for_edge') or payload.get('reconnect'):
        _, current = cloudflare.connection()
        updated = {**current, 'oauth': token, 'client': payload['client']}
        updated.pop('token', None)
        # Reauthorization must still grant access to the same account and zone.
        if current.get('zone'):
            zone = cloudflare.Client(updated).call('GET', '/zones/' + current['zone'])
            if current.get('account') and zone['account']['id'] != current['account']:
                raise domains.DomainProblem('Authorize the account that owns the connected domain.')
        cloudflare.save(updated, 'connecting')
        jobs.enqueue('edge.enable' if payload.get('for_edge') else 'domain.connect', max_attempts=100)
    else:
        cloudflare.save({'oauth': token, 'client': payload['client']}, 'checking')
        jobs.enqueue('domain.connect')


def access_token(data):
    token = data['oauth']
    if token.get('expires_at', 0) > time.time() + 60:
        return token['access_token']
    row, current = cloudflare.connection()
    version = row.version
    if current['oauth'].get('expires_at', 0) > time.time() + 60:
        data['oauth'] = current['oauth']
        return current['oauth']['access_token']
    token = _request({'grant_type': 'refresh_token', 'client_id': current['client']['client_id'],
                      'refresh_token': current['oauth']['refresh_token']})
    token['refresh_token'] = token.get('refresh_token', current['oauth']['refresh_token'])
    token['expires_at'] = time.time() + int(token.get('expires_in', 300))
    current['oauth'] = token
    updated = db.session.execute(update(HostingConnection).where(HostingConnection.id == 1,
        HostingConnection.version == version).values(payload=cloudflare.encrypt(current), version=version + 1))
    db.session.commit()
    if updated.rowcount != 1:
        raise jobs.RetryLater('The hosting connection changed while refreshing access. Retrying.')
    data['oauth'] = token
    return token['access_token']


def revoke(data):
    if not data.get('oauth'):
        return
    try:
        with requests.Session() as http:
            http.trust_env = False
            response = http.post(REVOKE, data={'client_id': data['client']['client_id'],
                'token': data['oauth']['refresh_token'], 'token_type_hint': 'refresh_token'}, timeout=(3, 10), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError
    except (requests.RequestException, ValueError):
        raise domains.DomainProblem('Cloudflare could not revoke OAuth access. Retry disconnect or revoke the app in Cloudflare.') from None
