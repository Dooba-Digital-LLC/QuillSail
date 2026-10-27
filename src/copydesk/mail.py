"""Sending email.

Backends (``[mail] backend`` in copydesk.toml):

* ``smtp``: a real mail server, such as your university's relay or a provider
  like Amazon SES, Postmark, Mailgun or Purelymail.
* ``console``: print messages to the log instead of sending them (development).
* ``file``: write each message to a .eml file (for testing on a server).
* ``memory``: keep messages in ``mail.outbox`` (automated tests).

QuillSail never sends email from a web request. Messages are queued as
background jobs and the worker sends them, pacing itself to stay under
``rate_per_minute``.
"""

from __future__ import annotations

import contextlib
import logging
import smtplib
import ssl
import time
from dataclasses import asdict, dataclass, field, replace
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any

from copydesk.config import MailConfig
from copydesk.utils import email_domain, random_token, utcnow

log = logging.getLogger("copydesk.mail")

# Messages sent with the "memory" backend land here.
outbox: list[EmailMessage] = []


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None
    from_name: str = ""
    from_email: str = ""
    reply_to: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    category: str = "notification"

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Email:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class MailError(Exception):
    """Sending failed. ``permanent`` means retrying won't help (e.g. the address doesn't exist)."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


def build_message(email: Email, config: MailConfig) -> EmailMessage:
    from_email = email.from_email or config.from_email
    msg = EmailMessage()
    msg["Subject"] = email.subject
    msg["From"] = formataddr((email.from_name, from_email)) if email.from_name else from_email
    msg["To"] = email.to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=email_domain(from_email) or "localhost")
    if email.reply_to:
        msg["Reply-To"] = email.reply_to
    for name, value in email.headers.items():
        if name not in msg:
            msg[name] = value
    msg.set_content(email.text or "")
    if email.html:
        from copydesk.email_assets import inline_logos

        html, logos = inline_logos(email.html)
        msg.add_alternative(html, subtype="html")
        html_part = msg.get_payload()[-1]
        for logo in logos:
            html_part.add_related(logo.data, maintype="image", subtype="png", cid=f"<{logo.cid}>",
                                  disposition="inline", filename="publication-logo.png")
    return msg


class Mailer:
    """Sends messages, reusing one SMTP connection for a batch.

    Use it as a context manager::

        with Mailer(config) as mailer:
            for email in emails:
                mailer.send(email)
    """

    def __init__(self, config: MailConfig | None = None, *, throttle: bool = True) -> None:
        from copydesk.email_connections import effective_mail_config

        self.current_sender = config is None
        self.config = config or effective_mail_config()
        self.throttle = throttle and self.config.backend in {"smtp", "gmail", "outlook"}
        self._smtp: smtplib.SMTP | None = None
        self._last_sent = 0.0

    def __enter__(self) -> Mailer:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._smtp is not None:
            with contextlib.suppress(smtplib.SMTPException, OSError):
                self._smtp.quit()
            self._smtp = None

    def _connect(self) -> smtplib.SMTP:
        cfg = self.config
        smtp: smtplib.SMTP | None = None
        try:
            if cfg.security == "ssl":
                smtp = smtplib.SMTP_SSL(
                    cfg.host, cfg.port, timeout=cfg.timeout, context=ssl.create_default_context()
                )
            else:
                smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
                smtp.ehlo()
                if cfg.security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
            if cfg.username:
                smtp.login(cfg.username, cfg.password)
        except smtplib.SMTPAuthenticationError as exc:
            if smtp is not None:
                smtp.close()
            raise MailError(f"The mail server rejected the username or password ({exc.smtp_code}).") from exc
        except (smtplib.SMTPException, OSError) as exc:
            if smtp is not None:
                smtp.close()
            raise MailError(f"Couldn't connect to the mail server at {cfg.host}:{cfg.port}: {exc}") from exc
        return smtp

    def _wait_for_rate_limit(self) -> None:
        if not self.throttle:
            return
        interval = 60.0 / max(1, self.config.rate_per_minute)
        wait = self._last_sent + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def send(self, email: Email) -> str:
        """Send one message and return its Message-ID."""
        if self.current_sender or self.config.backend in {"gmail", "outlook"}:
            email = replace(email, from_email=self.config.from_email)
        message = build_message(email, self.config)
        backend = self.config.backend
        self._wait_for_rate_limit()
        if backend == "memory":
            outbox.append(message)
        elif backend == "console":
            log.info("Email to %s: %s\n\n%s", email.to, email.subject, email.text)
        elif backend == "file":
            directory = Path(self.config.file_dir)
            directory.mkdir(parents=True, exist_ok=True)
            name = f"{utcnow():%Y%m%d-%H%M%S}-{random_token(4)}.eml"
            (directory / name).write_bytes(message.as_bytes())
        elif backend in {"gmail", "outlook"}:
            from copydesk.email_connections import ConnectionProblem, send_api

            try:
                send_api(self.config, message)
            except ConnectionProblem as exc:
                # A provider-wide authorization failure must never count as a recipient bounce.
                raise MailError(str(exc)) from None
        else:
            self._send_smtp(message, email.to)
        self._last_sent = time.monotonic()
        return message["Message-ID"]

    def _send_smtp(self, message: EmailMessage, to: str) -> None:
        for attempt in range(2):
            if self._smtp is None:
                self._smtp = self._connect()
            try:
                self._smtp.send_message(message, to_addrs=[to])
                return
            except smtplib.SMTPRecipientsRefused as exc:
                code, reason = next(iter(exc.recipients.values()), (550, b"refused"))
                raise MailError(
                    f"The server refused {to}: {code} {_decode(reason)}", permanent=code >= 500
                ) from exc
            except smtplib.SMTPResponseException as exc:
                if exc.smtp_code >= 500:
                    raise MailError(f"{exc.smtp_code} {_decode(exc.smtp_error)}", permanent=True) from exc
                raise MailError(f"Temporary failure: {exc.smtp_code} {_decode(exc.smtp_error)}") from exc
            except (smtplib.SMTPServerDisconnected, OSError) as exc:
                # The connection went stale; reconnect once before giving up.
                self._smtp = None
                if attempt:
                    raise MailError(f"Lost the connection to the mail server: {exc}") from exc


def _decode(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def check_connection(config: MailConfig | None = None) -> str | None:
    """Try to connect and sign in to the SMTP server. Returns a problem, or None if fine."""
    from copydesk.email_connections import ConnectionProblem, check_api, effective_mail_config

    try:
        config = config or effective_mail_config()
        if config.backend in {"gmail", "outlook"}:
            check_api(config)
            return None
    except ConnectionProblem as exc:
        return str(exc)
    if config.backend != "smtp":
        return "Choose Gmail, Outlook, or an SMTP server to send real email."
    mailer = Mailer(config, throttle=False)
    try:
        mailer._smtp = mailer._connect()
        return None
    except MailError as exc:
        return str(exc)
    finally:
        mailer.close()
