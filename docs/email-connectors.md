# Connect Gmail or Outlook

Administrators manage publication email under **Settings → Email**. Gmail and Outlook are the main options. SMTP settings and configuration-file import remain available under **Other email providers**. The current server email configuration keeps working until a new sender has connected successfully.

## Gmail sign-in

Click **Connect Gmail**, choose your Google or Google Workspace account, and approve sending. Fresh publications automatically discover QuillSail's shared application. Users do not enter a client ID or client secret or register an OAuth app. Discovery runs when sign-in starts, not at application startup or page rendering. Temporary service failures leave SMTP and custom app setup available.

Unlike Cloudflare's public PKCE client, Google's Web application has a confidential client secret. It stays on the website backend and is never published in discovery, source or desktop downloads. The website exchanges authorization codes and renews tokens with Google; user tokens pass through that service without central storage. Each publication keeps its own access token, refresh token and token-bound renewal proof encrypted with its instance secret key. Email content goes directly from the publication to Google's API, not through the website.

Sign-in uses PKCE and the fixed HTTPS callback `https://quillsail.blog/oauth/gmail/callback`. The confirmation page names the destination publication and requires a click before forwarding the code. Desktop returns to its private loopback dashboard even when the publication has a public domain. Authorization state is bound to the initiating administrator/browser and expires after ten minutes. A saved client snapshot prevents hosted metadata changes from redirecting an in-flight token exchange.

The broker permits only the fixed Google token endpoint and registered callback. It rejects broader/inbox-reading scopes, unexpected credentials, browser forms, oversized requests and unsigned or mismatched refresh proofs. Tokens and provider errors are not echoed into logs or public responses. Valid refreshes are checked against the active sender before updating the encrypted connection.

### Availability and Google verification

The maintainer must configure and activate the shared service. Google applications in **Testing** allow only listed test users, are subject to a user cap, and testing authorizations expire after seven days. Public use of the sensitive `gmail.send` scope requires Google's publishing and verification process. Shared credentials cannot remove those restrictions. A disabled or unavailable service reports a connection error and keeps the current sender unchanged. See [Google's audience rules](https://support.google.com/cloud/answer/15549945) and [scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).

### Maintainer setup, once for all publications

Configure the private website environment with `QUILLSAIL_GOOGLE_CLIENT_ID`, `QUILLSAIL_GOOGLE_CLIENT_SECRET` and a random persistent `QUILLSAIL_GMAIL_BROKER_SECRET` of at least 32 characters. Retain the website's existing `QUILLSAIL_OAUTH_RELAY_SECRET`. Register the exact HTTPS website callback on the Google Web application; preserve callbacks used by existing installations. Enable the Gmail API and declare only `openid email https://www.googleapis.com/auth/gmail.send`.

Provide the required app branding, verified domains and privacy policy, publish the Google app appropriately, and complete sensitive-scope verification for public distribution. Set `QUILLSAIL_GMAIL_ENABLED=1` only when the intended Google audience is configured; enabling it while the Google app is in Testing still permits only listed test users. The default is disabled. Application code does not claim that verification has happened merely because credentials exist.

Back up the private environment, especially the broker proof key. Changing that key invalidates existing shared renewal proofs and requires reconnecting affected publications. Rotating the Google client secret on the backend does not distribute a new secret to publications. Website metadata at `/.well-known/quillsail/gmail.json` contains only the public client ID, send-only scope and HTTPS endpoints. See [website configuration](../website/README.md#configuration).

### Use a custom Gmail application

Organizations and forks can still use **Use your own Gmail app (optional)**. Custom registrations take precedence over shared discovery:

1. Open [Google Cloud credentials](https://console.cloud.google.com/apis/credentials), create/select a project, and enable the Gmail API.
2. Configure the OAuth consent screen for your organization or external users. During testing, add the sender to the app's test users. Google's testing and verification rules apply to broader deployment.
3. Create an OAuth client of type **Web application**. Add the exact Gmail callback URL shown in QuillSail. For an example installation it is `https://newsletter.example.org/dashboard/settings/email/callback/gmail`.
4. Enter the client ID and client secret in QuillSail's Gmail app setup, or upload Google's downloaded web-client credentials JSON. Save the setup.
5. Click **Connect Gmail**, select the account, and grant the requested permissions. Clear both custom app fields to restore the installation's default registration.

QuillSail requests `gmail.send` and basic account identification (`openid email`). It sends MIME messages through the Gmail API and does not request inbox-reading scopes. Offline access lets the worker refresh authorization while the administrator is signed out. If Google does not return a refresh token, QuillSail asks the administrator to reconnect instead of accepting a connection that cannot send in the background.

See [Google's authorization guide](https://developers.google.com/identity/protocols/oauth2/web-server), [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), and [sending messages](https://developers.google.com/workspace/gmail/api/guides/sending).

## Outlook and Microsoft 365

1. Open [Microsoft Entra app registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) and register an application. Select the supported account type that matches the intended users. Personal Outlook.com and work/school accounts require the corresponding multi-tenant/personal-account option.
2. Add a **Web** redirect URI matching the exact Outlook callback shown in QuillSail. An example callback is `https://newsletter.example.org/dashboard/settings/email/callback/outlook`.
3. Add delegated Microsoft Graph permissions **Mail.Send** and **User.Read**. QuillSail also requests OpenID account identification and `offline_access`. Your organization may require administrator consent.
4. Create a client secret and enter its **value**, rather than its secret identifier, along with the application/client ID in QuillSail.
5. Use `common` to permit the app's supported personal and organizational accounts, or enter your tenant ID/domain to limit connections. Save the setup, click **Connect Outlook**, and send a test.

Microsoft app credentials can also be imported as JSON containing `client_id`, `client_secret`, and optionally `tenant`. QuillSail uses Graph's MIME `sendMail` API to preserve HTML/plain-text alternatives and newsletter headers. User.Read identifies the chosen mailbox; no Mail.Read permission is requested.

See [Microsoft's authorization flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow) and [Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

## Configuration files and SMTP

Admins can upload a `copydesk.toml` containing `[mail]`, or an SMTP `.env` file, up to 64 KB. Only recognized email keys are imported; other instance settings and sudo credentials are ignored. Files are parsed as data and never executed or expanded as shell commands.

Recognized environment names include `COPYDESK_MAIL_*` names and common aliases: `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USE_SSL`, `SMTP_USER`/`SMTP_USERNAME`, `SMTP_PASSWORD`/`SMTP_PASS`/`WEBMAIL_APP_PASSWORD`, `MAIL_FROM`/`SMTP_FROM`, and `SMTP_RATE_PER_MINUTE`.

Alternatively enter SMTP settings directly. QuillSail checks the connection before activating them. A saved dashboard password can be kept blank only when the host, port, TLS mode, and username remain the same; changing these requires entering the appropriate password. Sign-in requires TLS or STARTTLS. An unauthenticated campus relay can use the relay option.

## Server-side app registration

Instead of entering provider app credentials in the dashboard, the operator can set:

```toml
[oauth]
google_client_id = "..."
google_client_secret = "..."
microsoft_client_id = "..."
microsoft_client_secret = "..."
microsoft_tenant = "common"
```

Environment overrides are `COPYDESK_GOOGLE_CLIENT_ID`, `COPYDESK_GOOGLE_CLIENT_SECRET`, `COPYDESK_MICROSOFT_CLIENT_ID`, `COPYDESK_MICROSOFT_CLIENT_SECRET`, and `COPYDESK_MICROSOFT_TENANT`. The unprefixed `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, and `MICROSOFT_TENANT` aliases are also supported. Pass these into the web and worker processes; the server does not automatically source a neighboring .env file.

For a different shared broker, set `oauth.google_metadata_url` or `COPYDESK_GOOGLE_METADATA_URL` to its HTTPS discovery document. Its token and callback endpoints must use the same origin. Set this URL to an empty string to disable discovery. Explicit custom Google credentials remain local and connect directly to Google; they are never sent to the shared service. Desktop ignores server environment overrides and uses the shared default unless its own publication has a custom registration.

## Storage, switching, and recovery

Only verified QuillSail administrators can change publication senders or app credentials. OAuth state is single-use, expires after ten minutes, and is tied to the initiating browser, administrator, authentication version, provider, and app registration. Authorization uses PKCE. OAuth codes, passwords, and tokens are never reflected into the setup page or audit entries.

Dashboard SMTP credentials, client secrets, and OAuth tokens are encrypted using a key derived from the instance's `site.secret_key`. Back up that configuration together with the database. Losing/changing the secret key requires restoring it or reconnecting email. Raw server configuration remains protected by its file permissions. Provider HTTP errors are converted to messages that do not expose token responses.

One sender is active for the publication. A new connection replaces it after successful authorization; cancelled or failed sign-in keeps the existing sender. Switching takes effect in subsequent jobs/batches, and queued mail uses the selected sender. Version checks prevent an in-flight token refresh from restoring a disconnected account. Provider authorization failures do not count as recipient bounces.

**Disconnect** removes the saved sender and resumes the server configuration. To revoke the app grant itself, use the Google or Microsoft account's app-permissions page. Provider app-credential changes apply to new connections; reconnect an existing account when rotating its provider secret. Provider limits, spam policies, and organization rules still apply; use a suitable SMTP relay/provider for larger newsletters.

Upgrade existing instances with `quillsail upgrade` before restarting the web and worker processes. Migration 0002 adds the encrypted connection and temporary OAuth-flow tables; it leaves existing SMTP settings and publication content intact.
