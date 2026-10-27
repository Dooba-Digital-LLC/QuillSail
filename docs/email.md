# Email and delivery

Administrators can now connect Gmail or Outlook from **Settings → Email**. SMTP and configuration-file upload are available as alternatives. See the [email connector guide](email-connectors.md) for the one-time app registration and account connection steps.

Configure the university's approved SMTP relay in `[mail]`:

```toml
backend = "smtp"
from_email = "newsletter@example.edu"
host = "smtp.example.edu"
port = 587
security = "starttls"
username = "newsletter@example.edu"
password = ""
rate_per_minute = 60
```

Supply the password through `COPYDESK_MAIL_PASSWORD` or a protected configuration file. Ask IT for the supported port, transport security, sender address, credentials, and rate limit. Use `ssl` for implicit TLS (usually port 465); `none` is for a trusted local relay only. Arrange the sender domain's SPF and DKIM with IT and verify delivery before launch.

Run `copydesk worker` continuously with the same config as the web process, or schedule `copydesk jobs run --max-time 55` once a minute. The worker sends confirmation messages, notifications, scheduled issues, and test emails. An absent worker leaves these queued. Console and file backends do not deliver messages; file messages go to `data/mail` by default.

Readers confirm their address before receiving newsletters. A verified signed-in account can subscribe its own address immediately. Newsletter messages include HTML and plain-text bodies, preference links, and one-click unsubscribe headers. Mail-app one-click unsubscribe uses POST; visiting the unsubscribe link displays a confirmation page.

Compose an issue, preview both formats and audience variants, send a test to your own inbox, then confirm its issue number on the Send page. Sending is asynchronous. Check delivery reports and `copydesk jobs list --status failed`; use the dashboard to retry failed deliveries or `copydesk jobs retry-failed` for failed jobs.

Delivery status means the SMTP server accepted the message, not that it reached an inbox. There is no open/click tracking or inbound bounce processor. Immediate permanent SMTP refusals are recorded; repeated refusals can disable a subscriber. A lost connection after SMTP acceptance can result in a duplicate on retry: SMTP delivery cannot guarantee exactly-once delivery.

Audience selections on email signup are preferences, not verified group membership. Approval-controlled account groups determine permissions on the website. Do not use email audience labels as access control for confidential material.

Restricted article bodies are never used to generate automatic summaries. Editors may deliberately supply an excerpt, subtitle, or custom issue summary; those teasers can be public. Removing an article from publication also removes it from public issue bodies. A previously used subscription confirmation link cannot reactivate an unsubscribed address; the reader must start a new signup or use their private preference link.
