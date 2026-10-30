# Set up your publication

Sign in as the administrator you created during installation, then work through **Dashboard → Settings**. Settings are stored in the database and take effect straight away; nothing needs a restart. The [settings reference](reference/settings.md) lists every option and its default.

## Name and identity

Under **Settings → General**, set:

- **Publication name**, shown at the top of every page and in every email.
- **Tagline**, one short line under the name.
- **Description**, a sentence or two used by search engines and link previews.
- **Published by** and **Organization website**, for the club, department or organization behind the publication.
- **Contact email**, shown in the footer so readers can reach the editors.
- **Time zone**. Dates are shown, and issues scheduled, in this time zone.
- **Footer text**, optional Markdown at the bottom of every page.

The publication's name, logo and colors are independent of QuillSail's. Readers see your brand; QuillSail adds only a small "Powered by QuillSail" credit in the footer.

## Look and feel

**Settings → Appearance** opens the publication designer. Start from one of five styles, then adjust type, colors, layout, the homepage hero and email presentation, previewing each change before you save. See [design and themes](theming.md).

## Pages and sections

Under **Pages**, edit the starter **About**, **Writing guidelines** and **Privacy** pages, or add your own. Pages are written in Markdown and can appear in the site's navigation. The writing guidelines page is linked from the editor, so writers can check the rules as they go.

Under **Sections**, organize articles into departments. New sites start with News, Features, Tutorials, Opinion and Community. A section can stop accepting submissions while keeping its existing articles.

## Who can join

Under **Settings → Accounts**, choose **Who can create an account**:

- **Anyone**.
- **Only people with an email address at the domains below**, such as `example.edu`. Subdomains are included, so `example.edu` also allows `mail.example.edu`.
- **Nobody**: administrators add people.

You can also show your reader groups on the sign-up form, and require new members to agree to a page, such as a code of conduct.

To add someone yourself, open **Dashboard → Members** and add them by email. QuillSail emails them an invitation to set their password. From the command line, `quillsail user create` makes a confirmed account and `quillsail user add-group` puts someone in a group.

Under **Dashboard → Members** you can also block an account. A blocked person is signed out at once and can't sign back in. Only administrators can block another administrator.

## Submissions

Under **Settings → Writing**, decide how the **Write for us** page behaves:

- **Accept submissions**. When it's off, the Write page explains that submissions are closed, using your own message.
- **Write page introduction**, in Markdown.
- **Writing guidelines page**, linked from the editor.
- **Writers must pick a section** before submitting.
- **Word limit**. Writers see a warning when they go over. `0` means no limit.

## Newsletter defaults

Under **Settings → Newsletter**, set the sign-up heading and text, the reply-to address, and the default subject line. The subject can use `{site}`, `{number}` and `{title}`, as in `{site} #{number}: {title}`. Also choose whether articles in emails show a summary or the full text, write the welcome email, and add an email footer.

Many email providers and anti-spam laws expect newsletters to include a physical **mailing address**. Add yours here.

To let another website, such as your club's main site, host a sign-up form, list its address under **Websites allowed to embed the sign-up form**. See the [public API](api.md#subscription-endpoint).

## Email

Until you connect a sender, email isn't delivered: depending on the installation, it's written to files (the desktop app and Docker Compose) or to the server log. Connect Gmail, Outlook or an SMTP server under **Settings → Email**; see [Gmail, Outlook and SMTP](email-connectors.md). Then send yourself a test message, and check [email and delivery](email.md) before your first real issue.

## Before you invite readers

- [ ] Publication name, description and contact email are set.
- [ ] About, Writing guidelines and Privacy pages say what you mean.
- [ ] Reader groups and their joining rules suit your community. See [groups and permissions](groups-and-permissions.md).
- [ ] Editors are in the **Editors** group.
- [ ] A sender is connected, and a test email arrived in a real inbox.
- [ ] The mailing address is set under **Settings → Newsletter**.
- [ ] On a server: backups are scheduled. See [backups and upgrades](maintenance.md).
