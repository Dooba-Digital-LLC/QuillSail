import base64
import hashlib
import io
import json
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

import pytest
import responses
from sqlalchemy import select

from copydesk import email_connections as ec
from copydesk import jobs
from copydesk.db import db
from copydesk.mail import Email, Mailer, MailError
from copydesk.models import (
    AuditEntry,
    EmailConnection,
    EmailOAuthFlow,
    Group,
    GroupMembership,
    Setting,
    User,
)
from copydesk.utils import utcnow


@pytest.fixture
def http():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def configure(app, provider):
    with app.app_context():
        ec.save_client(provider, "test-client", "CLIENT_SECRET_NEVER_DISPLAY", "common")
        db.session.commit()


def authorize(client, app, provider):
    configure(app, provider)
    response = client.post(
        f"/dashboard/settings/email/connect/{provider}", data={"csrf_token": "test-csrf"}
    )
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"][0].endswith(f"/dashboard/settings/email/callback/{provider}")
    assert "CLIENT_SECRET_NEVER_DISPLAY" not in response.location
    return query


def register_exchange(http, provider, *, refresh=True, email="sender@example.com"):
    token = {
        "access_token": "ACCESS_SECRET",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": ec.PROVIDERS[provider]["scope"],
    }
    if refresh:
        token["refresh_token"] = "REFRESH_SECRET"
    http.post(ec.PROVIDERS[provider]["token"].format(tenant="common"), json=token)
    profile = (
        {"email": email, "email_verified": True}
        if provider == "gmail"
        else {"mail": email, "id": "account-id"}
    )
    http.get(ec.PROVIDERS[provider]["profile"], json=profile)


def finish(client, provider, query):
    return client.get(
        f"/dashboard/settings/email/callback/{provider}",
        query_string={"state": query["state"][0], "code": "authorization-code"},
    )


def test_email_page_prioritizes_gmail_outlook_and_does_not_expose_credentials(editor_client, app):
    configure(app, "gmail")
    response = editor_client.get("/dashboard/settings/email")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.index("Connect Gmail") < html.index("Other email providers")
    assert html.index("Outlook") < html.index("Other email providers")
    assert "CLIENT_SECRET_NEVER_DISPLAY" not in html
    assert "https://accounts.google.com" in response.headers["Content-Security-Policy"]
    assert "https://login.microsoftonline.com" in response.headers["Content-Security-Policy"]


def test_settings_manager_cannot_change_email_credentials(client, app):
    with app.app_context():
        user = User(
            email="limited@example.test",
            username="limited",
            display_name="Limited",
            password_hash="none",
            email_verified_at=utcnow(),
        )
        role = Group(
            slug="settings-manager", name="Settings manager", permissions=["manage_settings"]
        )
        db.session.add_all([user, role])
        db.session.flush()
        db.session.add(GroupMembership(user=user, group=role, status="active"))
        db.session.commit()
        user_id, version = user.id, user.auth_version
    with client.session_transaction() as session:
        session["uid"], session["uv"], session["_csrf"] = user_id, version, "csrf"
    for path in ["/dashboard/settings/email", "/dashboard/settings/email/connect/gmail"]:
        response = client.post(path, data={"csrf_token": "csrf", "action": "disconnect"})
        assert response.status_code == 403


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_authorization_exchange_pkce_and_encrypted_credentials(editor_client, app, http, provider):
    query = authorize(editor_client, app, provider)
    register_exchange(http, provider)
    response = finish(editor_client, provider, query)
    assert response.status_code == 302
    with app.app_context():
        row, data = ec.connection_data()
        assert data["email"] == "sender@example.com"
        assert data["provider"] == provider
        assert (
            "REFRESH_SECRET" not in row.payload and "CLIENT_SECRET_NEVER_DISPLAY" not in row.payload
        )
        assert ec.effective_mail_config().backend == provider
        assert not db.session.scalars(select(EmailOAuthFlow)).all()
        assert all(
            "SECRET" not in json.dumps(entry.details)
            for entry in db.session.scalars(select(AuditEntry)).all()
        )
    token_request = next(call.request for call in http.calls if "/token" in call.request.url)
    body = parse_qs(token_request.body)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == query["code_challenge"][0]
    assert body["client_secret"] == ["CLIENT_SECRET_NEVER_DISPLAY"]
    assert finish(editor_client, provider, query).status_code == 400


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
@pytest.mark.parametrize("inline_logo", [False, True])
def test_api_send_uses_connected_sender_and_preserves_mime_headers(
    editor_client, app, http, provider, inline_logo
):
    query = authorize(editor_client, app, provider)
    register_exchange(http, provider)
    assert finish(editor_client, provider, query).status_code == 302
    http.post(
        ec.PROVIDERS[provider]["send"],
        json={"id": "message-id"} if provider == "gmail" else None,
        status=200 if provider == "gmail" else 202,
    )
    with app.app_context(), Mailer() as mailer:
        body = "<p>HTML</p>"
        if inline_logo:
            from PIL import Image
            from werkzeug.datastructures import FileStorage

            from copydesk import settings
            from copydesk.helpers import external_url
            from copydesk.uploads import save_image

            stream = io.BytesIO()
            Image.new("RGBA", (40, 10), (128, 0, 128, 255)).save(stream, "PNG")
            stream.seek(0)
            upload = save_image(FileStorage(stream=stream, filename="logo.png"), None, kind="logo")
            settings.set_value("appearance.email_logo", upload.path)
            db.session.commit()
            body += f'<img src="{external_url("public.upload", path=upload.path)}">'
        mailer.send(
            Email(
                to="reader@example.test",
                subject="Provider send",
                text="Plain",
                html=body,
                from_email="old-sender@example.test",
                headers={"List-Unsubscribe": "<https://example.test/unsubscribe/token>"},
            )
        )
    call = http.calls[-1].request
    if provider == "gmail":
        raw = base64.urlsafe_b64decode(json.loads(call.body)["raw"])
    else:
        raw = base64.b64decode(call.body)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert str(message["From"]) == "sender@example.com"
    assert message["List-Unsubscribe"] == "<https://example.test/unsubscribe/token>"
    html_body = message.get_body(preferencelist=("html",)).get_content().strip()
    if inline_logo:
        image = next(part for part in message.walk() if part.get_content_type() == "image/png")
        assert image.get_content_disposition() == "inline"
        assert "cid:" + str(image["Content-ID"]).strip("<>") in html_body
    else:
        assert html_body == "<p>HTML</p>"
    assert call.headers["Authorization"] == "Bearer ACCESS_SECRET"


def test_oauth_state_mismatch_expiry_and_denied_consent_preserve_existing_sender(
    editor_client, app
):
    query = authorize(editor_client, app, "gmail")
    assert (
        editor_client.get(
            "/dashboard/settings/email/callback/gmail?state=wrong&code=wrong"
        ).status_code
        == 400
    )
    with app.app_context():
        assert ec.effective_mail_config().backend == "memory"
        flow = db.session.scalar(select(EmailOAuthFlow))
        flow.created_at = utcnow() - timedelta(minutes=11)
        db.session.commit()
    assert finish(editor_client, "gmail", query).status_code == 400
    query = authorize(editor_client, app, "gmail")
    assert (
        editor_client.get(
            "/dashboard/settings/email/callback/gmail",
            query_string={"state": query["state"][0], "error": "access_denied"},
        ).status_code
        == 400
    )
    with app.app_context():
        assert ec.effective_mail_config().backend == "memory"


def test_missing_offline_access_does_not_activate_account(editor_client, app, http):
    query = authorize(editor_client, app, "gmail")
    register_exchange(http, "gmail", refresh=False)
    assert finish(editor_client, "gmail", query).status_code == 400
    with app.app_context():
        assert ec.effective_mail_config().backend == "memory"


def test_refresh_rotation_and_sender_change_race(editor_client, app, http):
    query = authorize(editor_client, app, "outlook")
    register_exchange(http, "outlook")
    finish(editor_client, "outlook", query)
    with app.app_context():
        row, data = ec.connection_data()
        data["token"]["expires_at"] = 0
        ec.save_connection(data)
        db.session.commit()
        config = ec.effective_mail_config()
        http.post(
            ec.PROVIDERS["outlook"]["token"].format(tenant="common"),
            json={
                "access_token": "NEW_ACCESS",
                "refresh_token": "ROTATED_REFRESH",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )
        assert ec._access_token(config) == "NEW_ACCESS"
        assert ec.connection_data()[1]["token"]["refresh_token"] == "ROTATED_REFRESH"
        ec.save_connection({"provider": "server"})
        db.session.commit()
        with pytest.raises(ec.ConnectionProblem):
            ec._access_token(config)


def test_provider_denial_is_not_a_recipient_bounce(editor_client, app, http):
    query = authorize(editor_client, app, "gmail")
    register_exchange(http, "gmail")
    finish(editor_client, "gmail", query)
    http.post(
        ec.PROVIDERS["gmail"]["send"],
        status=403,
        json={"error": {"message": "do not expose ACCESS_SECRET"}},
    )
    with app.app_context():
        with Mailer() as mailer, pytest.raises(MailError) as exc:
            mailer.send(Email(to="reader@example.test", subject="Denied", text="Body"))
        assert not exc.value.permanent
        assert "ACCESS_SECRET" not in str(exc.value)


def test_smtp_config_import_is_encrypted_and_secrets_are_not_reflected(
    editor_client, app, monkeypatch
):
    monkeypatch.setattr("copydesk.views.dashboard.email.check_connection", lambda config=None: None)
    content = b'SMTP_HOST=smtp.example.test\nSMTP_PORT=465\nSMTP_USE_SSL=true\nSMTP_USER=sender@example.test\nWEBMAIL_APP_PASSWORD="SMTP_SECRET"\nMAIL_FROM=sender@example.test\nSUDO_PASS=IGNORE_ME\n'
    response = editor_client.post(
        "/dashboard/settings/email",
        data={
            "csrf_token": "test-csrf",
            "action": "import_smtp",
            "config_file": (io.BytesIO(content), ".env"),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        row, data = ec.connection_data()
        assert data["smtp"]["password"] == "SMTP_SECRET"
        assert "SMTP_SECRET" not in row.payload and "IGNORE_ME" not in row.payload
    page = editor_client.get("/dashboard/settings/email").get_data(as_text=True)
    assert "SMTP_SECRET" not in page and "IGNORE_ME" not in page
    response = editor_client.post(
        "/dashboard/settings/email",
        data={
            "csrf_token": "test-csrf",
            "action": "smtp",
            "host": "bad host",
            "password": "FORM_SECRET",
        },
    )
    assert response.status_code == 422 and "FORM_SECRET" not in response.get_data(as_text=True)


def test_smtp_password_is_not_reused_for_a_new_host(app):
    with app.app_context():
        original = ec.validate_smtp(
            {
                "host": "smtp.one.test",
                "port": "465",
                "security": "ssl",
                "username": "user",
                "password": "secret",
                "from_email": "sender@example.test",
            }
        )
        edited = {**original, "host": "smtp.two.test", "password": ""}
        with pytest.raises(ec.ConnectionProblem):
            ec.validate_smtp(edited, original)


def test_client_secrets_never_reused_for_a_different_app(app):
    configure(app, "gmail")
    with app.app_context():
        with pytest.raises(ec.ConnectionProblem):
            ec.save_client("gmail", "different-app", "")
        row = db.session.get(Setting, ec.CLIENT_KEY + "gmail")
        assert "CLIENT_SECRET_NEVER_DISPLAY" not in row.value


def test_disconnect_restores_server_email_and_invalidates_old_connection(editor_client, app, http):
    query = authorize(editor_client, app, "gmail")
    register_exchange(http, "gmail")
    finish(editor_client, "gmail", query)
    response = editor_client.post(
        "/dashboard/settings/email", data={"csrf_token": "test-csrf", "action": "disconnect"}
    )
    assert response.status_code == 302
    with app.app_context():
        assert ec.effective_mail_config().backend == "memory"


def test_corrupt_saved_connection_is_recoverable_from_dashboard(editor_client, app):
    with app.app_context():
        ec.save_connection({"provider": "server"})
        db.session.commit()
        row = db.session.get(EmailConnection, 1)
        row.payload = "not-a-valid-ciphertext"
        db.session.commit()
    for path in ["/dashboard/", "/dashboard/system/", "/dashboard/settings/email"]:
        assert editor_client.get(path).status_code == 200
    assert (
        editor_client.post(
            "/dashboard/settings/email", data={"csrf_token": "test-csrf", "action": "disconnect"}
        ).status_code
        == 302
    )
    with app.app_context():
        assert ec.effective_mail_config().backend == "memory"


def test_missing_send_scope_and_changed_app_do_not_replace_sender(editor_client, app, http):
    query = authorize(editor_client, app, "gmail")
    http.post(
        ec.PROVIDERS["gmail"]["token"],
        json={
            "access_token": "access",
            "refresh_token": "refresh",
            "token_type": "Bearer",
            "scope": "openid email",
            "expires_in": 3600,
        },
    )
    assert finish(editor_client, "gmail", query).status_code == 400
    query = authorize(editor_client, app, "gmail")
    with app.app_context():
        ec.save_client("gmail", "changed-client", "changed-secret")
        db.session.commit()
    assert finish(editor_client, "gmail", query).status_code == 400
    with app.app_context():
        assert ec.effective_mail_config().backend == "memory"


def test_expired_oauth_flow_cleanup(app, admin):
    with app.app_context():
        db.session.add(
            EmailOAuthFlow(
                state_hash="a" * 64,
                user_id=admin,
                auth_version=1,
                provider="gmail",
                payload=ec.encrypt({"verifier": "old"}),
                created_at=utcnow() - timedelta(minutes=20),
            )
        )
        db.session.commit()
        jobs.maintenance()
        assert db.session.get(EmailOAuthFlow, "a" * 64) is None


def test_switch_during_refresh_cannot_restore_old_sender(editor_client, app, http):
    query = authorize(editor_client, app, "gmail")
    register_exchange(http, "gmail")
    finish(editor_client, "gmail", query)
    with app.app_context():
        row, data = ec.connection_data()
        data["token"]["expires_at"] = 0
        ec.save_connection(data)
        db.session.commit()
        original = ec.effective_mail_config()

        def during_refresh(request):
            ec.save_connection({"provider": "server"})
            db.session.commit()
            return (
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "access_token": "new",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "new-refresh",
                    }
                ),
            )

        http.reset()
        http.add_callback(responses.POST, ec.PROVIDERS["gmail"]["token"], callback=during_refresh)
        with pytest.raises(ec.ConnectionProblem):
            ec._access_token(original)
        assert ec.effective_mail_config().backend == "memory"


def test_test_email_uses_new_sender_and_worker_path(editor_client, app, http):
    query = authorize(editor_client, app, "gmail")
    register_exchange(http, "gmail")
    finish(editor_client, "gmail", query)
    http.post(ec.PROVIDERS["gmail"]["send"], json={"id": "test-message"})
    assert (
        editor_client.post(
            "/dashboard/settings/email", data={"csrf_token": "test-csrf", "action": "test"}
        ).status_code
        == 302
    )
    with app.app_context():
        assert jobs.run_pending(max_jobs=10) == 1
    assert http.calls[-1].request.url == ec.PROVIDERS["gmail"]["send"]
