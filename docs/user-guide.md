# Publication guide

## Set up your publication

Sign in as the administrator created during installation. Set your publication name and identify its publisher or organization. Choose its accent color and theme under Settings. Edit About, Writing guidelines, and Privacy under Pages. Configure timezone, contact details, registration policy, and email defaults.

Students, Staff, and Alumni are starter groups. Rename, replace, or add groups under Groups. Each has a membership policy, custom fields, badge, optional domain rules, and permissions. Open groups allow self-joining; approval groups create requests; closed groups require an administrator. A verified matching email domain can grant automatic membership where configured. Only use domain rules for domains that reliably identify the intended audience.

Everyone represents all visitors. Members represents confirmed accounts. Editors can review and publish, compose issues, and manage subscribers and pages. Administrators have all permissions. Grant member-management permission separately when editors should approve membership requests. Group permissions accumulate; subscriber audience preferences do not grant account permissions.

## Blog mode and newsletter reuse

Hosts choose **Newsletter**, **Blog**, or **Blog + newsletter** during browser setup, with `quillsail init --mode blog` / `--mode hybrid`, or later under **Settings → General → Publication type**. The default remains Newsletter. Switching modes preserves existing posts, issues, subscriptions, groups and permissions.

Blog modes use a chronological post home page and RSS link. Blog + newsletter also shows email subscriptions; Blog hides the newsletter calls to action. Existing issue and subscription URLs remain available so changing presentation does not break previously shared links. Use the Writing and Accounts settings to choose whether to accept community submissions or keep the blog for its existing authors.

Administrators and trusted accounts with **Publish articles** permission can publish their own blog posts directly from the writing editor and edit them later. Ordinary writers still submit for editorial review. Publishing a post does not create an issue or send a newsletter to subscribers.

On a published post, choose **Newsletter options**. A host with **Put issues together** permission can add the saved post to an existing draft issue or create a new draft, choosing a summary/link or full article. This reuses the same post rather than copying it; repeated additions to one issue do not duplicate it. Review and send the issue using the normal newsletter workflow. For this local newsletter feature, select Blog + newsletter.

For a separate newsletter, set **External newsletter website** in General settings to its QuillSail base URL, including any subpath (for example `https://newsletter.example.org`). From Newsletter options, **Continue to newsletter** opens its submission form carrying only the public post URL, title, author credit and excerpt. Restricted posts cannot use this handoff. Both sites must run a QuillSail version with blog-submission support. For other newsletter software, share the original post URL with its editors manually.

The writer signs into a verified account on the destination site, reviews the fields, supplies an excerpt of at least 20 words, confirms permission, and submits. Its normal submission permissions, sections and closure rules apply. The receiver does not fetch the source URL or accept sign-in authority from the blog. It creates a submission for editorial review, preserving original author credit and the source link in the published article, newsletter HTML/text and account export. Receiving editors decide whether to publish it or put it in an issue. A site that is closed to registration may require an invitation before the writer can submit.

## Writing and review

Readers create an account, confirm their email, and use Write for us to create a draft. Markdown supports links, code blocks, tables, footnotes, task lists, and note/tip/warning callouts. Save drafts and submit when ready. Unsubmitted drafts remain private to their authors. Writers can inspect revision history and follow feedback on the submission timeline.

Editors pick up submissions, leave feedback, request changes, accept, decline, or publish. Change requests require an explanation. Writers revise and resubmit. Internal editorial notes are hidden from writers. Events generate notifications according to account preferences.

## Newsletter issues

Create an issue under Issues. Set its title, subject override, introduction, preheader, and audience. Add accepted or published articles, headings, and text blocks. Reorder items, choose full articles or summaries, and optionally target individual blocks to audience groups. Members-only or group-restricted articles use summaries in email; check excerpts for sensitive information.

Preview HTML and text, including each audience variant. Send a test email. Schedule in the configured publication timezone or send immediately after confirming the issue number. The worker processes delivery. The report shows queued, sent, failed, and skipped addresses. An issue can also be published to the web without mailing subscribers.

## Competitions

Administrators can host competitions from **Dashboard → Competitions → New competition**. A group can also receive **Host competitions** permission. Set the brief, eligibility rules, criteria and prizes, then choose **Judges only**, **Reader voting only**, or **Judges and reader voting**. Administrators can judge automatically; appoint additional judges from verified active members. Assigned judges use the public entry pages and do not receive access to the rest of the dashboard.

Entrants submit a title, Markdown text and an optional project/repository link. Each verified account has one entry per competition. Organizers approve or reject entries with private feedback. Participants can revise or withdraw while submissions are open; revisions return the entry to pending approval. Approved entries and authors' display names become public when judging starts. Submitted work is frozen after that point.

Use the configured publication timezone for optional submission and judging/voting deadlines. The server enforces deadlines without requiring a background job. Leave deadlines blank to advance phases manually. The sequence is **Draft → Accepting entries → Judging and voting → Closed → Results published**. Opening a competition locks its setup; only drafts can be deleted.

Judges score approved entries from 0 to 100 against the stated criteria and may revise scores until judging closes. Their notes remain private to them. Verified readers have one vote per competition and may change or remove it until voting closes. Self-voting and self-judging are blocked. Rejected and withdrawn entries do not participate in standings. Before publication, scores and vote counts are visible only to organizers.

Close judging/voting and publish the final results. Judge mode requires at least one score for every approved entry. Judges' Choice ranks by average score, rounded to two decimals; Readers' Choice ranks by votes received. In both mode these are separate awards, without a combined weighted score. Tied entries share a rank; entries with zero reader votes do not receive a Readers' Choice rank. Published results are frozen, including if a judge or voter later deletes their account. Account exports include the user's entries, ballots and private judge notes. Account deletion removes ballots, private judge notes and unpublished entries; published competition work remains with anonymized credit.

## Subscribers and accounts

Subscribers need only an email address. Members have accounts for writing and access to community groups. Manage subscribers separately from members. Import/export commands are available through `quillsail subscribers --help`; only import addresses with appropriate permission to receive the publication. Accounts can edit profiles, notification preferences, subscriptions, group requests, and passwords, download personal data, and request account deletion. Deleting an account anonymizes it; already published writing may remain in the archive.
