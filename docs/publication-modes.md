# Blog and newsletter modes

A QuillSail publication is a **Newsletter**, a **Blog**, or a **Blog + newsletter**. Hosts choose during browser setup, with `quillsail init --mode blog` or `--mode hybrid`, or later under **Settings → General → Publication type**. The default is Newsletter.

Switching modes preserves existing posts, issues, subscriptions, groups and permissions.

## What changes between modes

| | Newsletter | Blog | Blog + newsletter |
| --- | --- | --- | --- |
| Homepage | The newsletter: latest issue and sign-up | Chronological posts | Chronological posts, with sign-up |
| Email subscriptions | Shown | Hidden | Shown |
| Newsletter issues | Yes | Existing issue pages stay available | Yes |

Blog modes use a chronological post homepage and an RSS link. Blog + newsletter also shows email subscriptions; Blog hides the newsletter calls to action. Existing issue and subscription URLs remain available, so changing presentation doesn't break previously shared links.

Use the **Writing** and **Accounts** settings to choose whether to accept community submissions or keep the blog for its existing authors.

## Publishing posts directly

Administrators and trusted accounts with the **Publish articles** permission can publish their own blog posts directly from the writing editor and edit them later. Ordinary writers still submit for editorial review. Publishing a post doesn't create an issue or send a newsletter to subscribers.

## Reusing a post in a newsletter

On a published post, choose **Newsletter options**. A host with the **Put issues together** permission can add the saved post to an existing draft issue or create a new draft, choosing a summary and link or the full article. This reuses the same post rather than copying it, and adding it to the same issue twice doesn't duplicate it. Review and send the issue using the normal [newsletter workflow](newsletter-issues.md). For this local newsletter feature, choose Blog + newsletter.

## Offering a post to another newsletter

A blog can offer its posts to a separately hosted QuillSail newsletter. Set **External newsletter website** in General settings to that newsletter's QuillSail base address, including any path, such as `https://newsletter.example.org`. From **Newsletter options**, **Continue to newsletter** opens its submission form carrying only the public post's address, title, author credit and excerpt. Restricted posts can't use this handoff. Both sites must run a QuillSail version with blog-submission support (1.3.0 or later). For other newsletter software, share the original post's address with its editors yourself.

The writer signs in to a confirmed account on the destination site, reviews the fields, supplies an excerpt of at least 20 words, confirms they have permission, and submits. The destination's normal submission permissions, sections and closing rules apply. A site that's closed to registration may require an invitation before the writer can submit.

The receiving site never fetches the source address and doesn't accept sign-in authority from the blog. It creates a submission for editorial review, keeping the original author credit and source link in the published article, the newsletter's HTML and text versions, and account exports. The receiving editors decide whether to publish it or put it in an issue.
