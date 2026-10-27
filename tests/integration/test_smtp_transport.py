"""SMTP tests use real loopback TCP/TLS connections and never relay externally."""

import re
import socket
import ssl
import time
from dataclasses import replace
from email import policy
from email.parser import BytesParser

import pytest

# ruff: noqa: E402 -- optional test dependencies must be checked before imports
pytest.importorskip('aiosmtpd')
trustme = pytest.importorskip('trustme')
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from copydesk import jobs, newsletter
from copydesk.config import MailConfig
from copydesk.db import db
from copydesk.mail import Email, Mailer, MailError, check_connection
from copydesk.models import User

pytestmark = [
    pytest.mark.integration,
    # The implicit-TLS fixture encrypts the socket before SMTP is constructed.
    pytest.mark.filterwarnings('ignore:Requiring AUTH while not requiring TLS:UserWarning'),
]


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class Inbox:
    def __init__(self):
        self.messages = []
        self.arrivals = []
        self.secure = []
        self.authenticated = []

    async def handle_RCPT(self, server, session, envelope, address, options):
        if address.startswith('permanent@'):
            return '550 5.1.1 Test recipient does not exist'
        if address.startswith('temporary@'):
            return '450 4.2.0 Test mailbox temporarily unavailable'
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(BytesParser(policy=policy.default).parsebytes(envelope.original_content))
        self.arrivals.append(time.monotonic())
        self.secure.append(bool(server.transport.get_extra_info('ssl_object')))
        self.authenticated.append(bool(session.authenticated))
        return '250 Message accepted by local test inbox'


def authenticate(server, session, envelope, mechanism, credentials):
    success = (isinstance(credentials, LoginPassword)
               and credentials.login == b'local-test-user'
               and credentials.password == b'local-test-password')
    return AuthResult(success=success, handled=False)


@pytest.fixture(params=['none', 'starttls', 'ssl'])
def smtp_server(request, tmp_path, monkeypatch):
    ca = trustme.CA()
    certificate = ca.issue_cert('localhost', '127.0.0.1')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(context)
    ca_path = tmp_path / 'ca.pem'
    ca.cert_pem.write_to_path(ca_path)
    monkeypatch.setenv('SSL_CERT_FILE', str(ca_path))
    mode = request.param
    inbox = Inbox()
    options = {'authenticator': authenticate, 'auth_required': mode != 'none'}
    if mode == 'starttls':
        options.update(tls_context=context, require_starttls=True)
    elif mode == 'ssl':
        options['ssl_context'] = context
        options['auth_require_tls'] = False  # implicit TLS already encrypts the socket
    controller = Controller(inbox, hostname='127.0.0.1', port=free_port(), **options)
    controller.start()
    config = MailConfig(
        backend='smtp', host='127.0.0.1', port=controller.port, security=mode,
        username='local-test-user' if mode != 'none' else '',
        password='local-test-password' if mode != 'none' else '',
        from_email='newsletter@example.test', rate_per_minute=100000, timeout=3,
    )
    try:
        yield config, inbox
    finally:
        controller.stop()


def test_real_smtp_mime_and_tls_authentication(smtp_server):
    config, inbox = smtp_server
    assert check_connection(config) is None
    email = Email(to='reader@example.test', subject='Transport verification', text='Plain text body',
                  html='<p>HTML body</p>', headers={'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click'})
    with Mailer(config) as mailer:
        message_id = mailer.send(email)
    assert len(inbox.messages) == 1
    if config.security != 'none':
        assert inbox.secure == [True]
        assert inbox.authenticated == [True]
    message = inbox.messages[0]
    assert message['Message-ID'] == message_id
    assert message['Subject'] == 'Transport verification'
    assert message['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
    assert message.get_body(preferencelist=('plain',)).get_content().strip() == 'Plain text body'
    assert message.get_body(preferencelist=('html',)).get_content().strip() == '<p>HTML body</p>'


def test_smtp_preserves_embedded_publication_logo(app, smtp_server):
    from io import BytesIO

    from PIL import Image
    from werkzeug.datastructures import FileStorage

    from copydesk import settings
    from copydesk.helpers import external_url
    from copydesk.uploads import save_image

    config, inbox = smtp_server
    with app.app_context():
        stream = BytesIO()
        Image.new('RGBA', (80, 20), (128, 0, 128, 255)).save(stream, 'PNG')
        stream.seek(0)
        upload = save_image(FileStorage(stream=stream, filename='logo.png'), None, kind='logo')
        settings.set_value('appearance.email_logo', upload.path)
        db.session.commit()
        email = Email(to='reader@example.test', subject='Embedded logo', text='Plain text',
                      html=f'<p>HTML</p><img src="{external_url("public.upload", path=upload.path)}">')
        with Mailer(config, throttle=False) as mailer:
            mailer.send(email)
    message = inbox.messages[0]
    image = next(part for part in message.walk() if part.get_content_type() == 'image/png')
    assert image.get_content_disposition() == 'inline'
    assert 'cid:' + str(image['Content-ID']).strip('<>') in message.get_body(preferencelist=('html',)).get_content()
    assert message.get_body(preferencelist=('plain',)).get_content().strip() == 'Plain text'


@pytest.mark.parametrize('address,permanent', [('permanent@example.test', True), ('temporary@example.test', False)])
def test_actual_smtp_refusals_are_classified(smtp_server, address, permanent):
    config, inbox = smtp_server
    with Mailer(config) as mailer, pytest.raises(MailError) as error:
        mailer.send(Email(to=address, subject='Refusal test', text='Body'))
    assert error.value.permanent is permanent
    assert inbox.messages == []


def test_bad_password_and_untrusted_certificate_are_rejected(smtp_server, monkeypatch):
    config, inbox = smtp_server
    if config.security == 'none':
        pytest.skip('TLS/authentication case')
    assert check_connection(replace(config, password='wrong-password')) is not None
    monkeypatch.delenv('SSL_CERT_FILE')
    assert check_connection(config) is not None
    assert inbox.messages == []


def test_signup_confirmation_issue_and_unsubscribe_over_smtp(smtp_server, app, client, admin):
    config, inbox = smtp_server
    app.extensions['copydesk'].config.mail = config
    assert client.post('/subscribe', data={'email': 'reader@example.test'}).status_code == 302
    with app.app_context():
        jobs.run_pending(max_jobs=10)
    confirmation = inbox.messages[0].get_body(preferencelist=('plain',)).get_content()
    token = re.search(r'/subscribe/confirm/([^\s<>]+)', confirmation).group(1)
    assert client.get(f'/subscribe/confirm/{token}').status_code == 200
    with app.app_context():
        jobs.run_pending(max_jobs=10)
        issue = newsletter.create(db.session.get(User, admin), 'Real SMTP issue')
        issue.intro = 'This message traverses a real local SMTP server.'
        newsletter.start_sending(issue, db.session.get(User, admin))
        db.session.commit()
        jobs.run_pending(max_jobs=20)
        assert newsletter.delivery_stats(db.session.get(type(issue), issue.id))['sent'] == 1
    message = inbox.messages[-1]
    assert 'Real SMTP issue' in str(message['Subject'])
    assert message['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
    secret = re.search(r'/unsubscribe/([^>]+)', str(message['List-Unsubscribe'])).group(1)
    assert client.post(f'/unsubscribe/{secret}', data={'List-Unsubscribe': 'One-Click'}).status_code == 200
    count = len(inbox.messages)
    with app.app_context():
        next_issue = newsletter.create(db.session.get(User, admin), 'No longer subscribed')
        next_issue.intro = 'This must not be delivered to the unsubscribed address.'
        newsletter.start_sending(next_issue, db.session.get(User, admin))
        db.session.commit()
        jobs.run_pending(max_jobs=20)
    assert len(inbox.messages) == count


def test_smtp_batch_rate_is_observed(smtp_server):
    config, inbox = smtp_server
    with Mailer(replace(config, rate_per_minute=300)) as mailer:
        for index in range(2):
            mailer.send(Email(to='reader@example.test', subject=f'Rate test {index}', text='Body'))
    assert inbox.arrivals[1] - inbox.arrivals[0] >= 0.18
