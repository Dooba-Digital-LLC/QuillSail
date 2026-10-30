# Subscribers and accounts

QuillSail keeps two lists. **Subscribers** get the newsletter by email and need only an address. **Members** have accounts, so they can write, join groups and read members-only articles. Manage subscribers under **Dashboard → Subscribers** and members under **Dashboard → Members**.

## Signing up

Readers subscribe from the sign-up form on your site, from a form on another website you allow, or through the [API](api.md#subscription-endpoint). Every sign-up is **double opt-in**: QuillSail emails a confirmation link, and the address gets no issues until someone clicks it. This keeps typos and fake sign-ups off your list, and many email providers expect it. Confirmation links last seven days.

After confirming, the subscriber gets the welcome email, if it's turned on under **Settings → Newsletter**.

A few details protect your readers:

- The sign-up form never reveals whether an address is already subscribed. An existing subscriber who signs up again gets a reminder email with their preferences link instead, and their choices aren't changed.
- A member with a confirmed account can subscribe their own address in one step, because they've already proved they own it.
- An old confirmation link can't reactivate an address that has unsubscribed. The reader signs up again or uses their preferences link.

## Audiences

If **Ask subscribers which groups they belong to** is on, the sign-up form offers your reader groups. These choices decide which parts of each issue a subscriber gets (see [newsletter issues](newsletter-issues.md#one-issue-every-audience)). They don't make anyone a member of a group and grant nothing on the website.

## Managing a subscription

Every newsletter includes a private **preferences link**. It lets the subscriber change their name and groups, or unsubscribe, without an account. Treat these links like passwords: anyone with the link can change that subscription.

Unsubscribing is always one step:

- the **unsubscribe link** in each email shows a confirmation page;
- mail apps that offer their own **Unsubscribe** button use one-click unsubscribe ([RFC 8058](https://www.rfc-editor.org/rfc/rfc8058)), which works without opening a page.

Members can also manage their subscription under **Account → Newsletter**.

## Statuses

| Status | Meaning |
| --- | --- |
| Pending | Signed up, but hasn't clicked the confirmation link yet. Gets no issues. |
| Active | Confirmed. Gets issues. |
| Unsubscribed | Left the list. Gets no issues. |
| Bounced | The mail server refused the address three times. Gets no issues. |

## Importing and exporting

People with **Manage subscribers** can import and export from **Dashboard → Subscribers**, or use the command line:

```sh
quillsail subscribers import readers.csv
quillsail subscribers export --status active --output subscribers.csv
```

An import file is a CSV with an `email` column and, optionally, `name` and `groups` columns. Groups can be listed by short name or name, separated by commas or semicolons. Each import stops after a fixed number of rows; split very large files.

By default, imported addresses are pending and get a confirmation email, like any other sign-up. Add `--confirmed` (or tick the option in the dashboard) to subscribe them straight away, but only when every person on the list already agreed to receive your publication. Importing people who didn't ask for your newsletter is the quickest way to get your email marked as spam.

Exports include each subscriber's status, groups, sign-up and confirmation dates, and source. Treat them as personal data.

## Accounts

People create an account under **Join**, subject to the rules in **Settings → Accounts**, then confirm their email address. Confirmation links last three days, and **Resend** sends a new one. Until they confirm, an account has only the permissions everyone has.

Passwords must be at least 10 characters, and common passwords are refused. A few random words work well. Too many failed sign-ins from one place pause further attempts for 15 minutes. Password reset links last two hours, and changing a password signs the account out everywhere else.

Under **Account**, members can:

- edit their profile, including the fields their groups ask for;
- ask to join or leave groups;
- manage their newsletter subscription;
- choose which notifications they get;
- change their email address, which they confirm from the new address, and their password;
- **download their data**: their profile, groups, writing, comments, subscriptions and competition activity;
- **delete their account**.

Deleting an account anonymizes it: the name, email address and profile are replaced, and the newsletter subscription and group memberships are removed. Unpublished pieces the person wrote alone are deleted. Published writing can stay in the archive, credited to a deleted account.
