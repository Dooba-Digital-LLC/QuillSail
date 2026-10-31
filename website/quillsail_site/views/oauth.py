"""Public-client OAuth relay: show the destination and require a deliberate click.

The relay never exchanges codes, sees verifiers, or stores provider tokens.
"""
from __future__ import annotations

import base64
import hmac
import json
import secrets
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, abort, current_app, redirect, render_template, request, session
from itsdangerous import BadData, URLSafeTimedSerializer

bp = Blueprint('oauth', __name__)


@bp.after_request
def private_responses(response):
    response.headers.update({'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
                             'X-Robots-Tag': 'noindex, nofollow'})
    return response


def destination(value, suffix='/dashboard/domain/oauth/callback'):
    try:
        parts = urlsplit(value)
        local = parts.hostname in {'127.0.0.1', '::1'}
        if (parts.scheme != 'https' and not (local and parts.scheme == 'http')) or not parts.hostname:
            raise ValueError
        if parts.username or parts.password or parts.query or parts.fragment or len(value) > 2048:
            raise ValueError
        if not parts.path.endswith(suffix) or any(c in value for c in '\r\n\\'):
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        if parts.netloc != parts.netloc.encode('ascii').decode():
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError):
        abort(400, description='The publication callback address is invalid.')


def serializer(provider='cloudflare'):
    salt = 'quillsail.hosting.relay.v1' if provider == 'cloudflare' else f'quillsail.{provider}.relay.v1'
    return URLSafeTimedSerializer(current_app.secret_key, salt=salt)


@bp.route('/oauth/cloudflare/callback', methods=['GET', 'POST'])
def callback():
    return confirmation('cloudflare', '/dashboard/domain/oauth/callback', 'Cloudflare')


def confirmation(provider, suffix, label):
    if not current_app.secret_key:
        abort(503, description='The OAuth relay has not been configured.')
    csrf_key = '_relay_csrf' if provider == 'cloudflare' else '_relay_csrf_' + provider
    if request.method == 'GET':
        state, code = request.args.get('state', ''), request.args.get('code', '')
        if not code or len(code) > 8192 or not state or len(state) > 4096:
            abort(400, description=label + ' did not return a usable authorization code.')
        try:
            decoded = json.loads(base64.urlsafe_b64decode(state + '=' * (-len(state) % 4)))
            target = destination(decoded['destination'], suffix)
            if not isinstance(decoded.get('nonce'), str) or len(decoded['nonce']) < 32:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            abort(400, description='The authorization state is invalid.')
        csrf = secrets.token_urlsafe(32)
        session[csrf_key] = csrf
        transfer = serializer(provider).dumps({'target': target, 'state': state, 'code': code, 'csrf': csrf})
        response = current_app.make_response(render_template('site/oauth.html', target=target, transfer=transfer, csrf=csrf, provider_name=label))
        # Chrome applies form-action to a form's 303 redirect as well. Permit
        # only the validated publication origin, never a wildcard destination.
        parts = urlsplit(target)
        target_origin = parts.scheme + '://' + parts.netloc
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self' " + target_origin
    else:
        csrf, expected = request.form.get('csrf', ''), session.get(csrf_key, '')
        if not csrf or not expected or not hmac.compare_digest(csrf, expected):
            abort(400, description='This form has expired. Start the connection again.')
        try:
            transfer = request.form.get('transfer', '')
            if len(transfer) > 20000:
                raise BadData('Too large')
            payload = serializer(provider).loads(transfer, max_age=600)
            if not hmac.compare_digest(payload['csrf'], expected):
                raise BadData('Wrong browser')
            target = destination(payload['target'], suffix)
        except (BadData, KeyError, TypeError):
            abort(400, description='This authorization has expired. Start again.')
        session.pop(csrf_key, None)
        # A top-level GET preserves the publication's SameSite=Lax login cookie.
        response = redirect(target + '?' + urlencode({'code': payload['code'], 'state': payload['state']}), 303)
    response.headers.update({'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
                             'X-Robots-Tag': 'noindex, nofollow'})
    return response
