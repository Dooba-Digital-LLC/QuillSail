from email import policy
from email.parser import BytesParser
from io import BytesIO

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from copydesk import appearance, newsletter, notify, settings
from copydesk.config import MailConfig
from copydesk.db import db
from copydesk.email_assets import inline_logos
from copydesk.helpers import external_url
from copydesk.mail import Email, Mailer, build_message, outbox
from copydesk.models import Job, Subscriber, User
from copydesk.uploads import save_image


def logo_file(format="PNG"):
    stream = BytesIO()
    image = Image.new("RGBA", (120, 30), (255, 0, 128, 0))
    image.putpixel((60, 15), (255, 0, 128, 255))
    image.save(stream, format)
    stream.seek(0)
    return FileStorage(stream=stream, filename="publication." + format.lower())


def set_logo(format="PNG", key="appearance.email_logo"):
    upload = save_image(logo_file(format), None, kind="logo")
    settings.set_value(key, upload.path)
    db.session.commit()
    return upload


def assert_embedded(message):
    # Parse the wire representation, as SMTP and provider APIs receive it.
    message = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert message.get_content_type() == "multipart/alternative"
    related = [part for part in message.walk() if part.get_content_type() == "multipart/related"]
    assert len(related) == 1
    images = [part for part in message.walk() if part.get_content_maintype() == "image"]
    assert len(images) == 1
    image = images[0]
    assert image.get_content_type() == "image/png"
    assert image.get_content_disposition() == "inline"
    cid = str(image["Content-ID"]).strip("<>")
    assert "cid:" + cid in message.get_body(preferencelist=("html",)).get_content()
    with Image.open(BytesIO(image.get_payload(decode=True))) as logo:
        assert logo.size == (120, 30)
        assert logo.convert("RGBA").getpixel((0, 0))[3] == 0
    return message


@pytest.mark.parametrize("role", ["appearance.logo", "appearance.email_logo"])
def test_uploaded_logo_is_embedded_when_queued_message_is_built(app, role):
    with app.app_context():
        upload = set_logo(key=role)
        source = external_url("public.upload", path=upload.path)
        email = Email(to="reader@example.test", subject="Branding", text="Plain text remains available.",
                      html=f'<p>HTML</p><img src="{source}" alt="Publication">',
                      headers={"List-Unsubscribe": "<https://example.test/unsubscribe/sample>"})
        queued = Email.from_payload(email.to_payload())
        with Mailer(MailConfig(backend="memory", from_email="sender@example.test")) as mailer:
            mailer.send(queued)
        message = assert_embedded(outbox[-1])
        assert message.get_body(preferencelist=("plain",)).get_content().strip() == email.text
        assert message["List-Unsubscribe"] == email.headers["List-Unsubscribe"]
        assert source not in message.get_body(preferencelist=("html",)).get_content()
        assert queued.html == email.html  # Browser/queued HTML keeps a normal public URL.


def test_newsletter_and_notification_paths_embed_the_logo_without_sending(app, admin):
    with app.app_context():
        set_logo()
        actor = db.session.get(User, admin)
        issue = newsletter.create(actor, "Logo verification")
        newsletter.add_block(issue, "text", "A story", "A useful paragraph.")
        subscriber = Subscriber(email="reader@example.test", status="active", token="synthetic-unsubscribe-token")
        email = newsletter.build_email(issue, subscriber, newsletter.prepare_items(issue, "#123456"))
        message = assert_embedded(build_message(email, MailConfig(from_email="sender@example.test")))
        assert "Unsubscribe" in message.get_body(preferencelist=("html",)).get_content()
        newsletter.send_test(issue, ["test@example.test"], None, actor)
        db.session.flush()
        from sqlalchemy import select

        job = db.session.scalar(select(Job).where(Job.kind == "mail.send").order_by(Job.id.desc()))
        assert_embedded(build_message(Email.from_payload(job.payload), MailConfig(from_email="sender@example.test")))
        notification = notify.compose("test", "reader@example.test", "Notification", user=actor)
        assert_embedded(build_message(notification, MailConfig(from_email="sender@example.test")))


def test_webp_logo_becomes_a_transparent_png_email_part(app):
    with app.app_context():
        upload = set_logo("WEBP")
        email = Email(to="reader@example.test", subject="WebP", text="Plain",
                      html=f'<img src="{external_url("public.upload", path=upload.path)}">')
        assert_embedded(build_message(email, MailConfig(from_email="sender@example.test")))


def test_unrelated_images_are_never_downloaded_or_embedded(app, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Logo embedding must not fetch URLs")

    import requests
    monkeypatch.setattr(requests.sessions.Session, "get", no_network)
    with app.app_context():
        set_logo()
        html = '<img src="https://example.test/private.png"><img src="http://127.0.0.1/internal">'
        result, images = inline_logos(html)
        assert result == html and not images


@pytest.mark.parametrize("path", ["2026/missing.png", "../private-logo.png"])
def test_missing_logo_and_traversal_leave_the_remote_fallback(app, tmp_path, path):
    with app.app_context():
        settings.set_value("appearance.email_logo", path)
        private = tmp_path / "private-logo.png"
        private.write_bytes(logo_file().read())
        html = f'<img src="{external_url("public.upload", path=path)}">'
        result, images = inline_logos(html)
        assert result == html and not images


def test_logo_symlink_cannot_embed_a_file_outside_uploads(app, tmp_path):
    with app.app_context():
        root = app.extensions["copydesk"].config.uploads_dir
        root.mkdir(parents=True, exist_ok=True)
        private = tmp_path / "private-logo.png"
        private.write_bytes(logo_file().read())
        (root / "linked.png").symlink_to(private)
        settings.set_value("appearance.email_logo", "linked.png")
        html = f'<img src="{external_url("public.upload", path="linked.png")}">'
        result, images = inline_logos(html)
        assert result == html and not images


def test_identical_logos_have_one_related_part(app):
    with app.app_context():
        first = set_logo(key="appearance.logo")
        second = set_logo(key="appearance.email_logo")
        html = "".join(f'<img src="{external_url("public.upload", path=upload.path)}">' for upload in (first, second))
        assert_embedded(build_message(Email(to="reader@example.test", subject="Two logos", text="Plain", html=html),
                                      MailConfig(from_email="sender@example.test")))


@pytest.mark.parametrize("prefix", ["", "/newsletter"])
def test_email_logo_url_uses_public_https_origin_behind_http_proxy(app, prefix):
    app.extensions["copydesk"].config.base_url = "https://publication.example.test" + prefix
    with app.test_request_context("/", base_url="http://backend.internal" + prefix + "/"):
        upload = set_logo()
        assert appearance.image_url("email_logo", external=True) == "https://publication.example.test" + prefix + "/uploads/" + upload.path
