# Newsletter issues

An issue is one numbered edition of your newsletter. It's an ordered list of articles, headings and text blocks, and any of them can be aimed at particular reader groups. Put issues together under **Dashboard → Issues**, which needs the **Put issues together** permission. Sending needs **Send issues**.

## Put an issue together

Create an issue, then fill in **About this issue**:

- **Title**, shown on the web and used in the default subject line.
- **Subject**, to override the default subject format from **Settings → Newsletter**.
- **Introduction**, in Markdown, shown at the top.
- **Preheader**, the short preview line many mail apps show after the subject.
- **Audience**: everyone who's subscribed, or only subscribers in particular reader groups.

Then add **contents**:

- **Articles**: accepted or published articles. Choose a summary and link, the full text, or the creator's newsletter blurb and link when one was submitted with a [blog article](publication-modes.md#offering-a-post-to-another-newsletter). For the summary option, write a custom summary or leave it blank for the article's usual summary. Custom summaries do not overwrite creator blurbs or full articles. These choices apply to the web issue and email.
- **Section headings**, to divide the issue.
- **Text blocks**, in Markdown, for announcements and notes.

Reorder items as you go. An article can appear only once in an issue.

> [!NOTE]
> Members-only and group-restricted articles always appear in email as a summary, never in full, and the summary is never generated from the restricted text. Check the excerpt or custom summary before sending.

## One issue, every audience

Any item can be limited to particular reader groups. A subscriber gets the items meant for their groups, plus every item meant for everyone. For example:

| Item | For |
| --- | --- |
| Article: *How I set up my first home lab* | Everyone |
| Heading: *For alumni* | Alumni |
| Text: *Come back and mentor* | Alumni |
| Text: *Internship fair on Friday* | Students |

Alumni get three items, students get two, and everyone else gets one. Subscribers choose their groups when they sign up, and can change them from the link in every email. These choices only decide what they receive; they grant no access on the website. See [groups and permissions](groups-and-permissions.md).

## Preview and test

**Preview** shows the issue as it will look on the web and in email, in both its HTML and plain-text versions. Under **Show the version for**, switch between groups to see exactly what each audience gets.

**Send a test** emails the issue to up to ten addresses, for any audience version. Always send yourself a test and check it in a real mail app before a real send: browser previews can't show how every mail app will display it.

To get feedback from someone without an account, share a [private preview link](business-outreach.md#share-a-draft-issue-for-feedback). It expires, and you can revoke it.

## Send or schedule

QuillSail lists anything that would stop the issue being sent, such as an article that's no longer accepted, or a group audience with no groups picked.

- **Send now** starts sending immediately. To confirm, type the issue's number.
- **Or schedule it** for a time in the future, in the publication's time zone. **Unschedule** cancels a scheduled send.

You can also publish an issue on the website without emailing subscribers.

### What happens when you send

1. The issue becomes *Sending*. It's published on the website, along with any accepted articles in it.
2. QuillSail works out who receives it and records one delivery for each subscriber.
3. The worker sends the deliveries in batches, over one connection to the mail server per batch, pacing itself to stay under `[mail] rate_per_minute`.
4. When every delivery is finished, the issue becomes *Sent*.

Sending happens in the background, so you can close the page. Starting a send is protected against double clicks and two editors pressing Send at once: each subscriber gets one delivery. Every delivery is saved as it's sent, so a problem late in a batch doesn't lose the ones before it.

> [!WARNING]
> No mail system can promise exactly-once delivery. If the worker stops after the mail server accepts a message but before QuillSail records it, that message can be sent again when the work resumes.

## Delivery reports

The issue's report shows how many deliveries are **waiting**, **sent**, **failed** and **skipped**, and lists the failed addresses. Retry failed deliveries from the report. Each delivery is tried up to three times.

"Sent" means your mail server accepted the message, not that it reached an inbox. QuillSail doesn't track opens or clicks, and doesn't process bounce messages that arrive later. When a mail server refuses an address outright, QuillSail records it, and after three such refusals the subscriber is marked as bounced and no longer receives issues.

## Before your first real send

- [ ] A sender is connected under **Settings → Email**, and a test arrived in a real inbox.
- [ ] Your sending domain has SPF and DKIM records. Ask whoever runs your email.
- [ ] `[mail] rate_per_minute` matches your provider's limit.
- [ ] The worker is running. **Dashboard → System status** shows its last heartbeat.
- [ ] The mailing address is set under **Settings → Newsletter**.

See [email and delivery](email.md) for the details.
