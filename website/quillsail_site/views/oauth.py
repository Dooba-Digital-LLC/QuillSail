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


def destination(value):
    try:
        parts = urlsplit(value)
        local = parts.hostname in {'127.0.0.1', '::1'}
        if (parts.scheme != 'https' and not (local and parts.scheme == 'http')) or not parts.hostname:
            raise ValueError
        if parts.username or parts.password or parts.query or parts.fragment or len(value) > 2048:
            raise ValueError
        if not parts.path.endswith('/dashboard/domain/oauth/callback') or any(c in value for c in '\r\n\\'):
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        if parts.netloc != parts.netloc.encode('ascii').decode():
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError):
        abort(400, description='The publication callback address is invalid.')


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='quillsail.hosting.relay.v1')


@bp.route('/oauth/cloudflare/callback', methods=['GET', 'POST'])
def callback():
    if not current_app.secret_key:
        abort(503, description='The OAuth relay has not been configured.')
    if request.method == 'GET':
        state, code = request.args.get('state', ''), request.args.get('code', '')
        if not code or len(code) > 8192 or not state or len(state) > 4096:
            abort(400, description='Cloudflare did not return a usable authorization code.')
        try:
            decoded = json.loads(base64.urlsafe_b64decode(state + '=' * (-len(state) % 4)))
            target = destination(decoded['destination'])
            if not isinstance(decoded.get('nonce'), str) or len(decoded['nonce']) < 32:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            abort(400, description='The authorization state is invalid.')
        csrf = secrets.token_urlsafe(32)
        session['_relay_csrf'] = csrf
        transfer = serializer().dumps({'target': target, 'state': state, 'code': code, 'csrf': csrf})
        response = current_app.make_response(render_template('site/oauth.html', target=target, transfer=transfer, csrf=csrf))
        # Chrome applies form-action to a form's 303 redirect as well. Permit
        # only the validated publication origin, never a wildcard destination.
        parts = urlsplit(target)
        target_origin = parts.scheme + '://' + parts.netloc
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self' " + target_origin
    else:
        csrf, expected = request.form.get('csrf', ''), session.get('_relay_csrf', '')
        if not csrf or not expected or not hmac.compare_digest(csrf, expected):
            abort(400, description='This form has expired. Start the connection again.')
        try:
            transfer = request.form.get('transfer', '')
            if len(transfer) > 20000:
                raise BadData('Too large')
            payload = serializer().loads(transfer, max_age=600)
            if not hmac.compare_digest(payload['csrf'], expected):
                raise BadData('Wrong browser')
            target = destination(payload['target'])
        except (BadData, KeyError, TypeError):
            abort(400, description='This authorization has expired. Start again.')
        session.pop('_relay_csrf', None)
        # A top-level GET preserves the publication's SameSite=Lax login cookie.
        response = redirect(target + '?' + urlencode({'code': payload['code'], 'state': payload['state']}), 303)
    response.headers.update({'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
                             'X-Robots-Tag': 'noindex, nofollow'})
    return response
