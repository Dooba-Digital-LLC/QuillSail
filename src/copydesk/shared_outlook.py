"""Shared Microsoft Web registration; its confidential secret stays on the website."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

import requests

from copydesk.email_connections import PROVIDERS, ConnectionProblem

MAX_BYTES = 16384


def _request(url, payload=None):
    try:
        with requests.Session() as http:
            http.trust_env = False
            with http.request('POST' if payload is not None else 'GET', url, json=payload,
                    timeout=(3, 10), allow_redirects=False, stream=True,
                    headers={'Accept': 'application/json'}) as response:
                content = bytearray()
                for chunk in response.iter_content(4096):
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ValueError
                if response.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError
                if response.status_code != 200:
                    permanent = data.get('error') in {'invalid_grant', 'invalid_client', 'invalid_proof', 'invalid_scope'}
                    raise ConnectionProblem('Outlook authorization needs to be renewed. Reconnect Outlook in Settings → Email.'
                        if permanent else 'Shared Outlook sign-in is temporarily unavailable. Retry shortly or use SMTP under Other email providers.',
                        temporary=not permanent)
                return data
    except (requests.RequestException, ValueError, TypeError):
        raise ConnectionProblem('Shared Outlook sign-in is temporarily unavailable. Retry shortly or use SMTP under Other email providers.', temporary=True) from None


def _https(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c in value for c in '\r\n\\'):
        raise ValueError
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        raise ValueError
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise ValueError
    return parts


def discover(configuration):
    try:
        source = configuration.get('metadata_url', '')
        origin = _https(source)
        data = _request(source)
        if (type(data.get('version')) is not int or data['version'] != 1
                or set(data) != {'version', 'client_id', 'relay_url', 'token_url', 'scope', 'tenant'}
                or not isinstance(data['client_id'], str)
                or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', data['client_id'])
                or not isinstance(data['tenant'], str)
                or not re.fullmatch(r'common|organizations|consumers|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', data['tenant'])
                or not isinstance(data['scope'], str)
                or set(data['scope'].split()) != set(PROVIDERS['outlook']['scope'].split())):
            raise ValueError
        for name, suffix in (('relay_url', '/oauth/outlook/callback'), ('token_url', '/oauth/outlook/token')):
            target = _https(data[name])
            if ((target.hostname, target.port or 443) != (origin.hostname, origin.port or 443)
                    or not target.path.endswith(suffix)):
                raise ValueError
        return {key: value for key, value in data.items() if key != 'version'} | {'shared': True}
    except (ValueError, TypeError, KeyError):
        raise ConnectionProblem('The shared Outlook app configuration is unavailable. Retry shortly or use SMTP under Other email providers.', temporary=True) from None


def token(client, *, code=None, verifier=None, previous=None):
    payload = {'client_id': client['client_id']}
    if previous is None:
        payload.update(grant_type='authorization_code', code=code, code_verifier=verifier,
                       redirect_uri=client['relay_url'])
    else:
        payload.update(grant_type='refresh_token', refresh_token=previous['refresh_token'],
                       refresh_proof=previous.get('refresh_proof', ''))
    result = _request(client['token_url'], payload)
    if not isinstance(result.get('refresh_proof'), str) or not result['refresh_proof']:
        raise ConnectionProblem('Outlook background access could not be verified. Reconnect Outlook.')
    try:
        lifetime = int(result['expires_in'])
        if not 0 < lifetime <= 86400:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ConnectionProblem('Outlook returned an unusable authorization lifetime. Reconnect Outlook.') from None
    result['expires_at'] = time.time() + lifetime
    return result
