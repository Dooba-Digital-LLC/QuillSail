# Business outreach and private previews

## Let businesses reach your editors

Administrators can open **Dashboard → Business outreach** and enable **Accept business inquiries**. The `/business/` form then appears as **For businesses** in public navigation. Share that URL with businesses or Workforce Development partners, or include it in a newsletter.

The form accepts internships, job openings, student projects, workforce challenges, mentorship, events, partnership conversations, and feedback. It collects the organization, contact name and email, opportunity details, and optional skills, location, compensation, deadline, and application link. No account is required. Contact information goes to the private inbox; the submitter can separately permit publication of their email address.

The contact receives a confirmation message through the normal email queue. They open its link and confirm the request. Links expire after three days. Email confirmation verifies control of the mailbox; staff still need to check employer identity, opportunity details, pay, and suitability before publication.

Users with **Manage business outreach** can review requests, filter the inbox by status, save private follow-up notes, reply using their email client, or delete an inquiry. Administrators have this permission automatically; other groups must be granted it explicitly. Updating the intake setting also requires **Change site settings**.

After a confirmed request is approved, staff with **Write and submit** can create an editorial draft. It copies the organization and opportunity details, plus the email address only if the submitter allowed publication. It excludes the private contact name and staff notes. The draft follows the normal review workflow; creating it never publishes an article or sends an issue. Repeating the handoff opens the existing draft.

Keep in mind that a business can put contact information in the description itself. Editors should read the complete draft before publishing it. Changes made later to an inquiry do not overwrite an editorial draft.

## Share a draft issue for feedback

Open an issue's **Preview** screen and choose **Share a private preview**. Choose a readable address such as `example` or let the application generate one, then choose a 7-, 30-, or 90-day expiry. Copy the complete generated URL, including its secret `key` query parameter.

Anyone with the complete URL can view the preview without an account. The bare address and invalid or expired keys return 404. A preview remains a draft, is omitted from public issue lists, search, sitemaps, and feeds, and is served with no-index, no-referrer, and private/no-store headers. Readers can switch between the web presentation and actual email layout.

Only the introduction, public-audience blocks, and selected articles whose visibility is public are shared. Selected unpublished public articles appear inline; restricted articles and group-only items are excluded. This includes unpublished writing, so check the preview before sharing it.

The secret is shown only when the link is created; the database stores its hash. To get another link, generate a replacement, which invalidates the old one. **Revoke link** removes access immediately. Scheduling or publishing the issue also disables the preview. These links are a sharing capability: recipients can forward them, so send them only to the intended reviewers.

To upgrade an existing installation, back up its database and configuration, install the new version, run `quillsail upgrade`, and restart the web and worker services. Migration `0005` adds the business inbox and preview-link tables without altering existing issue content.
