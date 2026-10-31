"""Confidential Microsoft client operations, with no central user-token storage."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time

import certifi
import urllib3
from flask import Blueprint, abort, current_app, jsonify, request
from itsdangerous import BadData, URLSafeSerializer

from quillsail_site.views.oauth import confirmation

bp = Blueprint('outlook', __name__)
SCOPE = 'openid email offline_access https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Send'
SEND_SCOPE = 'mail.send'
# Microsoft may include the basic OIDC profile alongside User.Read; no inbox scopes.
ALLOWED_SCOPES = {'openid', 'email', 'profile', 'offline_access', 'user.read', 'mail.send'}
TENANT = r'common|organizations|consumers|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
MAX_BYTES = 16384


def config():
    value = current_app.config['QUILLSAIL']
    if (not value.outlook_enabled or value.site.scheme != 'https' or not value.oauth_relay_secret
            or not value.microsoft_client_id or not value.microsoft_client_secret
            or len(value.outlook_broker_secret) < 32
            or not re.fullmatch(TENANT, value.microsoft_tenant)
            or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value.microsoft_client_id)):
        abort(503, description='Shared Outlook sign-in is not available yet.')
    return value


@bp.after_request
def private(response):
    if request.endpoint != 'outlook.metadata' or response.status_code != 200:
        response.headers['Cache-Control'] = 'private, no-store'
    response.headers.update({'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow'})
    return response


@bp.get('/.well-known/quillsail/outlook.json')
def metadata():
    value = config()
    response = jsonify(version=1, client_id=value.microsoft_client_id, scope=SCOPE, tenant=value.microsoft_tenant,
        relay_url=value.site.join('/oauth/outlook/callback'), token_url=value.site.join('/oauth/outlook/token'))
    response.headers['Cache-Control'] = 'public, max-age=300'
    return response


@bp.route('/oauth/outlook/callback', methods=['GET', 'POST'])
def callback():
    config()
    return confirmation('outlook', '/dashboard/settings/email/callback/outlook', 'Microsoft')


def signer(value):
    return URLSafeSerializer(value.outlook_broker_secret, salt='quillsail.outlook.refresh.v1',
                            signer_kwargs={'digest_method': hashlib.sha256})


def proof(value, refresh):
    return signer(value).dumps({'client': value.microsoft_client_id, 'tenant': value.microsoft_tenant,
                               'token': hashlib.sha256(refresh.encode()).hexdigest()})


def valid_proof(value, refresh, sent):
    try:
        decoded = signer(value).loads(sent)
        return (decoded['client'] == value.microsoft_client_id and decoded['tenant'] == value.microsoft_tenant
                and hmac.compare_digest(decoded['token'], hashlib.sha256(refresh.encode()).hexdigest()))
    except (BadData, KeyError, TypeError, AttributeError):
        return False


def limited():
    # Bounded, short-lived counters; neither credentials nor mailbox tokens are retained.
    limits, lock = current_app.extensions.setdefault('outlook_broker_limits', ({}, threading.Lock()))
    window = int(time.monotonic() // 60)
    key = request.remote_addr or 'unknown'
    with lock:
        for address in list(limits):
            if limits[address][0] != window:
                limits.pop(address)
        if len(limits) >= 2048 and key not in limits:
            return True
        prior, count = limits.get(key, (window, 0))
        count = count + 1 if prior == window else 1
        limits[key] = (window, count)
        return count > 60


def microsoft_request(fields, tenant):
    """Only this fixed Microsoft endpoint receives the private application secret."""
    if not re.fullmatch(TENANT, tenant):
        raise ValueError
    endpoint = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    pool = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where(),
        timeout=urllib3.Timeout(connect=3, read=10), retries=False)
    try:
        response = pool.request('POST', endpoint, fields=fields, encode_multipart=False,
                                redirect=False, preload_content=False)
        try:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
            return response.status, body
        finally:
            response.release_conn()
    finally:
        pool.clear()


def error(code, status=400):
    return jsonify(error=code), status


@bp.post('/oauth/outlook/token')
def token():
    value = config()
    # Server API: no cookie authentication, browser forms, CORS, or arbitrary proxies.
    if request.headers.get('Origin') or not request.is_json:
        return error('invalid_request')
    raw = request.stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return error('invalid_request', 413)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise ValueError
        grant = data.get('grant_type')
        if data.get('client_id') != value.microsoft_client_id:
            return error('invalid_client')
        fields = {'grant_type': grant, 'client_id': value.microsoft_client_id,
                  'client_secret': value.microsoft_client_secret, 'scope': SCOPE}
        refresh = None
        if grant == 'authorization_code':
            if (set(data) != {'grant_type', 'client_id', 'code', 'code_verifier', 'redirect_uri'}
                    or not 1 <= len(data['code']) <= 8192
                    or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', data['code_verifier'])
                    or data['redirect_uri'] != value.site.join('/oauth/outlook/callback')):
                raise ValueError
            fields.update(code=data['code'], code_verifier=data['code_verifier'], redirect_uri=data['redirect_uri'])
        elif grant == 'refresh_token':
            refresh = data.get('refresh_token', '')
            sent = data.get('refresh_proof', '')
            if (set(data) != {'grant_type', 'client_id', 'refresh_token', 'refresh_proof'}
                    or not 1 <= len(refresh) <= 8192 or not 1 <= len(sent) <= 2048
                    or not valid_proof(value, refresh, sent)):
                return error('invalid_proof')
            fields['refresh_token'] = refresh
        else:
            return error('unsupported_grant_type')
    except (ValueError, TypeError, KeyError):
        return error('invalid_request')
    if limited():
        return error('temporarily_unavailable', 429)
    try:
        status, upstream = microsoft_request(fields, value.microsoft_tenant)
        if status != 200:
            permanent = upstream.get('error') in {'invalid_grant', 'invalid_client', 'unauthorized_client'}
            return error('invalid_grant' if permanent else 'temporarily_unavailable', 400 if permanent else 503)
        allowed = {'access_token', 'refresh_token', 'expires_in', 'token_type', 'scope'}
        result = {key: val for key, val in upstream.items() if key in allowed}
        if not isinstance(result.get('access_token'), str) or not 1 <= len(result['access_token']) <= 8192:
            raise ValueError
        if not 0 < int(result.get('expires_in', 0)) <= 86400:
            raise ValueError
        granted = [scope.lower().removeprefix('https://graph.microsoft.com/') for scope in result.get('scope', '').split()]
        if SEND_SCOPE not in granted or any(scope not in ALLOWED_SCOPES for scope in granted):
            return error('invalid_scope')
        refresh = result.get('refresh_token') or refresh
        if not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192:
            raise ValueError
        result['refresh_proof'] = proof(value, refresh)
        return jsonify(result)
    except (urllib3.exceptions.HTTPError, ValueError, TypeError, AttributeError, OSError):
        return error('temporarily_unavailable', 503)
