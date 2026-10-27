# Deeper review — October 25, 2026

This follow-up reviewed access control and failure behavior beyond the initial page-render checks. Reproducible failures were converted into regression tests and fixed. No database schema change is required.

## Corrected behavior

- Group managers cannot change joining rules for roles whose privileges exceed their own. Administrator membership cannot be acquired through self-service or email-domain rules. Unverified administrator invitations do not replace the last confirmed administrator.
- Unverified accounts cannot manage an existing newsletter subscription by claiming the subscriber's address, and cannot read content through an unverified group membership.
- Restricted article bodies no longer become automatic public summaries or match public body searches. Editors can still explicitly publish teasers through excerpts, subtitles, and custom issue summaries.
- Public issue archives omit unpublished articles; targeted items are omitted from public issue metadata and the homepage issue contents.
- Email-change links are single-use and tied to the prior address and authentication version. Changing an address revokes old sessions and rotates a moved subscription's bearer token. Existing subscriptions at the new address are linked without retaining account authority over the old address.
- Old subscription confirmation links cannot reactivate an unsubscribed address.
- Writer submission actions enforce current permissions, submission closure, and required sections, including when invoked from the status page.
- Editorial comments no longer crash because their event payload conflicts with the hook method argument. Internal notes are not emailed to former reviewers. Failing extension handlers are logged without stopping later handlers.
- Completed newsletter deliveries are committed individually so a later batch error does not roll them back. Batch processing renews its job claim and worker heartbeat. SMTP acceptance followed by a crash before commit still has an unavoidable duplicate-delivery window.
- Starting an issue send uses a conditional database update, preventing two requests with stale draft state from dispatching it twice. The dashboard refuses to reset a running job, and worker completion respects claim ownership.
- Rate counters increment atomically across concurrent requests instead of losing increments.
- Malformed JSON and invalid body/group/order values return client errors. The subscription API supports repeated form group fields and respects disabled audience selection.
- Authenticated responses and token-bearing account/subscription pages are not publicly cacheable. Redirect validation rejects backslashes, control characters, and malformed URLs.
- Animated uploads are re-encoded with animation timing preserved and private metadata removed; decoding failures become form errors, and animation processing has a total pixel/frame limit.
- Invitation/password-reset confirmation applies the same group-domain and pending-subscription effects as normal email verification.
- Import refuses databases containing any application data, even when no accounts exist.

## Validation and limits

The automated suite covers public and dashboard rendering, login/session revocation, editorial transitions and comments, account verification, subscription confirmation and unsubscribe, audience isolation, adversarial permission checks, malformed requests, concurrent rate limiting, crash recovery, installation, and backup/restore. Tests use temporary SQLite databases and in-memory or mocked email. The wheel is also built and checked for imports and packaged resources.

The initial review used Python 3.13 and SQLite. A subsequent [deployment verification](verification.md) covers actual PostgreSQL/MariaDB servers, real SMTP transport and confirmed inbox delivery, Python 3.11–3.14, and the university deployment. See that report for current results and remaining limits. This remains an internal review, not an independent security audit; the package remains beta.
