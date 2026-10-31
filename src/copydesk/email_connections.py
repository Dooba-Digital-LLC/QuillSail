"""Publication senders: encrypted SMTP settings and delegated Gmail/Outlook accounts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import tomllib
from dataclasses import replace
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from authlib.integrations.requests_client import OAuth2Session
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app, session, url_for
from sqlalchemy import delete, update

from copydesk import settings
from copydesk.config import MailConfig
from copydesk.db import db
from copydesk.helpers import external_url, state
from copydesk.models import EmailConnection, EmailOAuthFlow
from copydesk.utils import is_valid_email, parse_int, utcnow

PROVIDERS = {
    "gmail": {
        "name": "Gmail",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "profile": "https://openidconnect.googleapis.com/v1/userinfo",
        "send": "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        "scope": "openid email https://www.googleapis.com/auth/gmail.send",
        "send_scope": "https://www.googleapis.com/auth/gmail.send",
    },
    "outlook": {
        "name": "Outlook",
        "authorize": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "profile": "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName",
        "send": "https://graph.microsoft.com/v1.0/me/sendMail",
        "scope": "openid email offline_access https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Send",
        "send_scope": "Mail.Send",
    },
}
CLIENT_KEY = "private.email.client."
FLOW_TTL = timedelta(minutes=10)
MAX_CONFIG_BYTES = 64 * 1024


class ConnectionProblem(Exception):
    def __init__(self, message, *, temporary=False):
        super().__init__(message)
        self.temporary = temporary


def _cipher():
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"copydesk.email.credentials.v1"
    ).derive(state().config.secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(data):
    return _cipher().encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()


def decrypt(value):
    try:
        data = json.loads(_cipher().decrypt(value.encode()))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (InvalidToken, ValueError, TypeError, AttributeError):
        raise ConnectionProblem(
            "The saved email credentials cannot be opened. Restore the instance secret key or reconnect email."
        ) from None


def client_config(provider):
    if provider not in PROVIDERS:
        raise ConnectionProblem("Choose Gmail or Outlook.")
    saved = settings.get(CLIENT_KEY + provider)
    return decrypt(saved) if saved else dict(state().config.oauth_clients.get(provider, {}))


def client_ready(provider, client=None):
    client = client if client is not None else client_config(provider)
    return bool(client.get('client_id') and client.get('client_secret') or
        provider in PROVIDERS and not client.get('client_id') and not client.get('client_secret') and client.get('metadata_url'))


def save_client(provider, client_id, client_secret, tenant="common"):
    previous = client_config(provider)
    client_id = str(client_id or "").strip()
    provided_secret = str(client_secret or "").strip()
    if provider in PROVIDERS and not client_id and not provided_secret:
        settings.set_value(CLIENT_KEY + provider, None)
        return
    if previous.get("client_id") != client_id and not provided_secret:
        raise ConnectionProblem("Enter the secret for the new app.")
    client_secret = provided_secret or previous.get("client_secret", "")
    if not client_id or not client_secret or len(client_id) > 500 or len(client_secret) > 4000:
        raise ConnectionProblem(
            "Enter the app client ID and client secret from Google or Microsoft."
        )
    tenant = str(tenant or "common").strip()
    if not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", tenant):
        raise ConnectionProblem(
            "Use common, organizations, consumers, or a Microsoft tenant ID or domain."
        )
    settings.set_value(
        CLIENT_KEY + provider,
        encrypt({"client_id": client_id, "client_secret": client_secret, "tenant": tenant}),
    )


def import_client(provider, content):
    try:
        data = json.loads(content)
        if provider == "gmail":
            data = data.get("web", data)
        if not isinstance(data, dict):
            raise ValueError
        save_client(
            provider, data.get("client_id"), data.get("client_secret"), data.get("tenant", "common")
        )
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ConnectionProblem(
            "Upload a web application credentials JSON file containing client_id and client_secret."
        ) from None


def _current():
    return db.session.get(EmailConnection, 1, populate_existing=True)


def connection_data():
    row = _current()
    return (row, decrypt(row.payload)) if row else (None, {"provider": "server"})


def save_connection(data):
    row = _current()
    identifier = uuid4().hex
    payload = encrypt(data)
    if row is None:
        row = EmailConnection(id=1, version=1, connection_id=identifier, payload=payload)
        db.session.add(row)
    else:
        row.version += 1
        row.connection_id, row.payload, row.updated_at = identifier, payload, utcnow()
    return row


def effective_mail_config():
    row, data = connection_data()
    base = state().config.mail
    provider = data.get("provider", "server")
    if provider == "server":
        return base
    if provider == "smtp":
        allowed = {
            key: value
            for key, value in data["smtp"].items()
            if key in MailConfig.__dataclass_fields__
        }
        return replace(base, **allowed, connection_id=row.connection_id)
    if provider in PROVIDERS:
        return replace(
            base,
            backend=provider,
            from_email=data["email"],
            username=data["email"],
            password="",
            host="",
            rate_per_minute=data.get("rate_per_minute", 60),
            connection_id=row.connection_id,
        )
    raise ConnectionProblem(
        "The saved email connection is not supported. Choose an email provider again."
    )


def public_status():
    try:
        config = effective_mail_config()
        row, data = connection_data()
        return {
            "provider": config.backend,
            "email": config.from_email,
            "label": PROVIDERS.get(config.backend, {}).get(
                "name", "SMTP" if config.backend == "smtp" else config.backend.title()
            ),
            "source": "server" if data.get("provider") == "server" else "dashboard",
            "connected": config.backend in {"smtp", *PROVIDERS},
            "problem": "",
            "host": config.host,
            "port": config.port,
            "security": config.security,
            "username": config.username,
            "rate_per_minute": config.rate_per_minute,
        }
    except ConnectionProblem as exc:
        return {
            "provider": "error",
            "label": "Email needs attention",
            "connected": False,
            "problem": str(exc),
            "email": "",
            "source": "dashboard",
        }


def validate_smtp(values, previous=None):
    host = str(values.get("host") or "").strip()
    sender = str(values.get("from_email") or "").strip().lower()
    username = str(values.get("username") or "").strip()
    password = str(values.get("password") or "")
    port = parse_int(values.get("port"))
    security = str(values.get("security") or "starttls").strip().lower()
    rate = parse_int(values.get("rate_per_minute", 60))
    if not host or len(host) > 253 or not re.fullmatch(r"[a-zA-Z0-9._:-]+", host):
        raise ConnectionProblem("Enter the SMTP server hostname without a URL or path.")
    if port is None or not 1 <= port <= 65535:
        raise ConnectionProblem("Enter a port between 1 and 65535.")
    if security not in {"ssl", "starttls", "none"}:
        raise ConnectionProblem("Choose TLS, STARTTLS, or an unauthenticated relay.")
    if not is_valid_email(sender):
        raise ConnectionProblem("Enter the sender email address.")
    if rate is None or not 1 <= rate <= 10000:
        raise ConnectionProblem("Choose a sending speed between 1 and 10,000 emails a minute.")
    if not password and previous and username:
        identity = ("host", "port", "security", "username")
        if all(str(values.get(key, "")) == str(previous.get(key, "")) for key in identity):
            password = previous.get("password", "")
    if password and not username:
        raise ConnectionProblem("Enter the SMTP username for this password.")
    if username and not password:
        raise ConnectionProblem(
            "Enter the SMTP password or app password. Changing servers requires entering its password again."
        )
    if security == "none" and (username or password):
        raise ConnectionProblem("Use TLS or STARTTLS when signing into an SMTP server.")
    if (
        any("\r" in value or "\n" in value for value in (username, sender))
        or len(username) > 500
        or len(password) > 4000
    ):
        raise ConnectionProblem("The SMTP sign-in settings are invalid.")
    return {
        "backend": "smtp",
        "host": host,
        "port": port,
        "security": security,
        "username": username,
        "password": password,
        "from_email": sender,
        "rate_per_minute": rate,
        "timeout": 30,
    }


def parse_smtp_file(content, filename):
    if len(content) > MAX_CONFIG_BYTES:
        raise ConnectionProblem("Use a configuration file smaller than 64 KB.")
    try:
        text = content.decode("utf-8-sig")
        if filename.lower().endswith(".toml"):
            data = tomllib.loads(text).get("mail", {})
        else:
            # dotenv_values parses data; it never evaluates shell code or expands variables.
            from io import StringIO

            from dotenv import dotenv_values

            env = dotenv_values(stream=StringIO(text), interpolate=False)
            names = {
                "host": ("COPYDESK_MAIL_HOST", "SMTP_HOST"),
                "port": ("COPYDESK_MAIL_PORT", "SMTP_PORT"),
                "security": ("COPYDESK_MAIL_SECURITY", "SMTP_SECURITY"),
                "username": ("COPYDESK_MAIL_USERNAME", "SMTP_USER", "SMTP_USERNAME"),
                "password": (
                    "COPYDESK_MAIL_PASSWORD",
                    "SMTP_PASSWORD",
                    "SMTP_PASS",
                    "WEBMAIL_APP_PASSWORD",
                ),
                "from_email": ("COPYDESK_MAIL_FROM_EMAIL", "MAIL_FROM", "SMTP_FROM"),
                "rate_per_minute": ("COPYDESK_MAIL_RATE_PER_MINUTE", "SMTP_RATE_PER_MINUTE"),
            }
            data = {
                key: next((env[n] for n in aliases if env.get(n) is not None), "")
                for key, aliases in names.items()
            }
            data["rate_per_minute"] = data["rate_per_minute"] or 60
            if not data["security"]:
                data["security"] = (
                    "ssl"
                    if str(env.get("SMTP_USE_SSL", "")).lower() in {"true", "yes", "1", "on"}
                    else "starttls"
                )
        if not isinstance(data, dict):
            raise ValueError
        if data.get("backend", "smtp") != "smtp":
            raise ConnectionProblem(
                "Upload SMTP settings, or connect Gmail or Outlook using the buttons above."
            )
        from email.utils import parseaddr

        data["from_email"] = parseaddr(str(data.get("from_email", "")))[1]
        return validate_smtp(data)
    except (ValueError, TypeError, UnicodeError):
        raise ConnectionProblem(
            "That file could not be read. Upload copydesk.toml with a [mail] section, or an SMTP .env file."
        ) from None


def callback_url(provider):
    return external_url("dash.email_callback", provider=provider)


def _endpoint(provider, name, client):
    if not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", str(client.get("tenant", "common"))):
        raise ConnectionProblem("The Microsoft tenant setting is invalid. Check the app setup.")
    return PROVIDERS[provider][name].format(tenant=client.get("tenant", "common"))


def _oauth_client(provider, client, **kwargs):
    return OAuth2Session(
        client["client_id"],
        client.get("client_secret", ""),
        token_endpoint_auth_method="none" if client.get('shared') else "client_secret_post",
        **kwargs,
    )


def _shared(provider):
    from copydesk import shared_gmail, shared_outlook

    return shared_outlook if provider == 'outlook' else shared_gmail


def start_oauth(provider, user):
    configuration = client_config(provider)
    if not client_ready(provider, configuration):
        raise ConnectionProblem(
            f"Complete the one-time {PROVIDERS[provider]['name']} app setup below, then connect your account."
        )
    url = state().config.base_url
    if not url.startswith("https://") and urlsplit(url).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ConnectionProblem("Use HTTPS for the publication before connecting email accounts.")
    client = configuration
    if not configuration.get('client_id') and not configuration.get('client_secret'):
        client = _shared(provider).discover(configuration)
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    if client.get('shared'):
        runtime = current_app.extensions.get('copydesk_desktop')
        destination = (runtime.url if runtime else state().config.origin) + url_for('dash.email_callback', provider=provider)
        nonce = base64.urlsafe_b64encode(json.dumps({'destination': destination, 'nonce': nonce}, separators=(',', ':')).encode()).decode().rstrip('=')
    db.session.execute(
        delete(EmailOAuthFlow).where(EmailOAuthFlow.created_at < utcnow() - FLOW_TTL)
    )
    flow = EmailOAuthFlow(
        state_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        user_id=user.id,
        auth_version=user.auth_version,
        provider=provider,
        payload=encrypt({"verifier": verifier, "client_fingerprint": _fingerprint(configuration),
                         "client": client if client.get('shared') else None}),
    )
    db.session.add(flow)
    session["email_oauth_state"] = nonce
    with _oauth_client(
        provider,
        client,
        scope=PROVIDERS[provider]["scope"],
        redirect_uri=client['relay_url'] if client.get('shared') else callback_url(provider),
        code_challenge_method="S256",
    ) as oauth:
        args = {"state": nonce, "code_verifier": verifier, "prompt": "consent select_account"}
        if provider == "gmail":
            args["access_type"] = "offline"
        else:
            # Microsoft permits one prompt value; consent also presents the account selection flow.
            args["prompt"] = "select_account"
        authorize, _ = oauth.create_authorization_url(
            _endpoint(provider, "authorize", client), **args
        )
    return authorize


def _fingerprint(client):
    return hashlib.sha256(json.dumps(client, sort_keys=True).encode()).hexdigest()


def finish_oauth(provider, user, sent_state, code):
    expected = session.get("email_oauth_state", "")
    if not sent_state or not expected or len(sent_state) > 4096 or not hmac.compare_digest(sent_state, expected):
        raise ConnectionProblem(
            "This sign-in did not start in your browser. Start the connection again."
        )
    digest = hashlib.sha256(sent_state.encode()).hexdigest()
    flow = db.session.get(EmailOAuthFlow, digest)
    if (
        flow is None
        or flow.provider != provider
        or flow.user_id != user.id
        or flow.auth_version != user.auth_version
        or flow.created_at < utcnow() - FLOW_TTL
    ):
        raise ConnectionProblem("This sign-in has expired. Start the connection again.")
    payload = decrypt(flow.payload)
    deleted = db.session.execute(delete(EmailOAuthFlow).where(EmailOAuthFlow.state_hash == digest))
    if deleted.rowcount != 1:
        raise ConnectionProblem("This sign-in has already been used. Start the connection again.")
    db.session.commit()
    session.pop("email_oauth_state", None)
    client = client_config(provider)
    if _fingerprint(client) != payload["client_fingerprint"]:
        raise ConnectionProblem("The app setup changed during sign-in. Start the connection again.")
    client = payload.get('client') or client
    if not code or len(code) > 8192:
        raise ConnectionProblem("Sign-in was cancelled or did not return an authorization code.")
    try:
        if client.get('shared'):
            token = _shared(provider).token(client, code=code, verifier=payload['verifier'])
        else:
            with _oauth_client(
                provider,
                client,
                scope=PROVIDERS[provider]["scope"],
                redirect_uri=callback_url(provider),
            ) as oauth:
                token = dict(
                    oauth.fetch_token(
                        _endpoint(provider, "token", client),
                        code=code,
                        code_verifier=payload["verifier"],
                        timeout=15,
                    )
                )
        _check_token(provider, token, require_refresh=True)
        email = _profile(provider, token["access_token"])
    except ConnectionProblem:
        raise
    except Exception:
        raise ConnectionProblem(
            "The provider could not finish sign-in. Check the app setup and account permissions, then try again."
        ) from None
    save_connection(
        {
            "provider": provider,
            "email": email,
            "token": token,
            "client": client,
            "rate_per_minute": 60,
        }
    )
    return email


def _check_token(provider, token, *, require_refresh=False):
    if not isinstance(token.get("access_token"), str) or not token["access_token"]:
        raise ConnectionProblem(
            "The provider did not return a usable access token. Reconnect the account."
        )
    if require_refresh and not token.get("refresh_token"):
        raise ConnectionProblem(
            "Background sending needs offline access. Reconnect and allow the requested permissions."
        )
    scope = token.get("scope")
    if scope:
        granted = scope.split() if isinstance(scope, str) else scope
        required = PROVIDERS[provider]["send_scope"].lower()
        if not any(s.lower() == required or s.lower().endswith("/" + required) for s in granted):
            raise ConnectionProblem(
                "Permission to send email was not granted. Reconnect and allow sending email."
            )


def _profile(provider, access_token):
    try:
        response = requests.get(
            PROVIDERS[provider]["profile"],
            headers={"Authorization": "Bearer " + access_token},
            timeout=15,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise ConnectionProblem(
                "The account could not be verified. Reconnect it, or check your organization’s app permissions."
            )
        data = response.json()
        email = (
            data.get("email", "")
            if provider == "gmail"
            else data.get("mail") or data.get("userPrincipalName", "")
        )
        if provider == "gmail" and data.get("email_verified") is not True:
            raise ConnectionProblem("Use a Google account with a verified email address.")
        if not is_valid_email(email):
            raise ConnectionProblem("That account does not have a usable sender email address.")
        return email.strip().lower()
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        raise ConnectionProblem(
            "The email provider could not verify the account. Please try connecting again."
        ) from None


def _access_token(config, *, force_refresh=False):
    row, data = connection_data()
    if (
        row is None
        or row.connection_id != config.connection_id
        or data.get("provider") != config.backend
    ):
        raise ConnectionProblem(
            "The email sender changed. This job will retry with the current sender.", temporary=True
        )
    version = row.version
    token = data["token"]
    if not force_refresh and token.get("expires_at", 0) > time.time() + 60:
        return token["access_token"]
    client = data["client"]
    try:
        if client.get('shared'):
            fresh = _shared(config.backend).token(client, previous=token)
        else:
            with _oauth_client(config.backend, client) as oauth:
                fresh = dict(
                    oauth.refresh_token(
                        _endpoint(config.backend, "token", client),
                        refresh_token=token["refresh_token"],
                        timeout=15,
                    )
                )
    except ConnectionProblem:
        raise
    except Exception as exc:
        # Never put token endpoint bodies, credentials, or OAuth codes into job logs.
        permanent = getattr(exc, "error", "") in {
            "invalid_grant",
            "invalid_client",
            "unauthorized_client",
        }
        raise ConnectionProblem(
            "Email authorization needs to be renewed. Reconnect the account in Settings → Email.",
            temporary=not permanent,
        ) from None
    fresh["refresh_token"] = fresh.get("refresh_token") or token["refresh_token"]
    _check_token(config.backend, fresh)
    data["token"] = fresh
    result = db.session.execute(
        update(EmailConnection)
        .where(
            EmailConnection.id == 1,
            EmailConnection.version == version,
            EmailConnection.connection_id == config.connection_id,
        )
        .values(payload=encrypt(data), version=version + 1, updated_at=utcnow())
    )
    db.session.commit()
    if result.rowcount != 1:
        raise ConnectionProblem(
            "The sender changed while refreshing authorization. The job will retry.", temporary=True
        )
    return fresh["access_token"]


def send_api(config, message):
    provider = config.backend
    for attempt in range(2):
        access_token = _access_token(config, force_refresh=bool(attempt))
        raw = message.as_bytes()
        headers = {"Authorization": "Bearer " + access_token}
        if provider == "gmail":
            kwargs = {"json": {"raw": base64.urlsafe_b64encode(raw).decode()}}
        else:
            headers["Content-Type"] = "text/plain"
            kwargs = {"data": base64.b64encode(raw)}
        try:
            response = requests.post(
                PROVIDERS[provider]["send"],
                headers=headers,
                timeout=30,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException:
            raise ConnectionProblem(
                "The email provider could not be reached. The job will retry.", temporary=True
            ) from None
        if response.status_code in {200, 202}:
            return
        if response.status_code == 401 and attempt == 0:
            continue
        temporary = response.status_code == 429 or response.status_code >= 500
        if response.status_code in {401, 403}:
            raise ConnectionProblem(
                "The email provider denied sending. Reconnect the account and check its permissions."
            )
        raise ConnectionProblem(
            "The email provider could not accept this message."
            + (" The job will retry." if temporary else ""),
            temporary=temporary,
        )


def check_api(config):
    email = _profile(config.backend, _access_token(config))
    if email != config.from_email:
        raise ConnectionProblem("The connected account changed. Reconnect the intended sender.")
