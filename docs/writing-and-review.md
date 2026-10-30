# Writing and review

Members write. Editors review. Every piece moves through a clear set of statuses, and both sides can see where it stands. This page follows a piece from first draft to publication.

## Writing a piece

Anyone with a confirmed account and the **Write and submit** permission can write. On the public site, choose **Write for us**, then start a new piece.

The editor has a Markdown text area with a live preview. A piece has:

- a **title** (up to 200 characters) and an optional **subtitle** (up to 300);
- the **body**, in Markdown;
- a **section** and **tags**, if your publication uses them;
- up to five **co-authors**, by username or email address, who must have confirmed accounts;
- an optional **cover image**, which needs a short description for people who can't see it;
- an optional **note to the editors**.

Save as often as you like. Drafts are private: only you and your co-authors can see them, even if you're also an editor.

### Markdown

QuillSail supports the usual Markdown, plus:

- tables, footnotes, task lists and ~~strikethrough~~;
- links that are typed as plain web addresses;
- code blocks with syntax highlighting, fenced with three backticks and a language name;
- callouts, in three kinds: `note`, `tip` and `warning`.

```text
::: tip An optional title
Callouts make a highlighted box.
:::
```

Raw HTML isn't rendered, and every page is sanitized before it's shown, so a piece can't add scripts or styles to your site. Extensions can add more Markdown features.

### Images

Upload images from the editor. QuillSail opens and re-saves every image to check it really is an image, applies the camera's rotation, shrinks very large photos, and removes the metadata cameras add, which can include the location where a photo was taken. The size limit is set by `[uploads] max_size_mb` in the server configuration, 10 MB by default.

Uploaded images are public once their address is known, even if the piece isn't. Don't upload anything confidential.

### Revision history

Each save that changes the text keeps a revision. Open **History** to see every revision with its author and word count, compare any revision with the one before, and restore an earlier version.

## Submitting

When the piece is ready, choose **Submit for review**. If your publication asks for it, choose a section first. If there's a word limit, the editor warns you when you're over it.

Once a piece is submitted, it's with the editors and you can't edit it until they ask for changes. You can still:

- follow its **timeline**, which shows each status change and comment;
- **comment** to talk with your editor;
- **withdraw** it, which stops the editors working on it. Later, **Move back to drafts** to keep working.

A draft that was never submitted can be deleted. After submission, withdraw it instead.

## Reviewing

Editors with **Review submissions** find new pieces under **Dashboard → Articles**. Open one to read it, see its timeline and act on it:

| Action | What happens |
| --- | --- |
| **Start review** | The piece moves to *In review*, so other editors know someone has it. |
| **Request changes** | The piece goes back to the writer to edit. You must say what to change. |
| **Accept** | The piece is ready to publish or to add to an issue. A note to the writer is optional. |
| **Decline** | The piece won't be published. A kind, specific note helps the writer. |
| **Publish now** | The piece goes live on the site. |
| **Unpublish** | The piece comes off the site and returns to *Accepted*. |
| **Reopen review** | A declined or withdrawn piece goes back to *In review*. |

The [editorial workflow reference](reference/workflow.md) lists every status and which actions apply to each.

A few rules keep things fair and safe:

- Editors can't publish someone else's draft before the writer submits it.
- Editors with **Edit any article** can fix a piece's text once it's submitted. Their edits are saved as revisions, with a short summary, and recorded in the activity log.
- You can **assign** a piece to a particular editor, who's then shown as responsible for it.

### Comments and internal notes

Comments on a piece's timeline go to its writer and the editors. An editor can instead post an **internal note**, which only people who can review submissions see. Use internal notes for editorial discussion the writer shouldn't read.

### Notifications

QuillSail emails people when something they care about happens:

- writers, when an editor comments, requests changes, accepts or publishes their piece;
- editors, when a piece is submitted or resubmitted;
- people who manage members, when someone asks to join an approval group.

Each person can turn these off under **Account → Notifications**. Every change is also recorded in the **activity log**.

## Publishing settings

Editors with **Publish articles** set each article's publishing settings in the dashboard:

- **Web address**: the slug in the article's URL.
- **Who can read it**: everyone, signed-in members, or members of particular reader groups.
- **Excerpt**: up to 400 characters, used in lists, feeds and newsletter summaries.
- **Featured**: highlight the article on the homepage.
- **Section**.

### Who can read a published article

| Visibility | Readers |
| --- | --- |
| Everyone | Anyone allowed to read the publication |
| Signed-in members | People with **Read members-only articles**, plus editors who can review |
| Particular groups | Confirmed members of the chosen reader groups, plus editors who can review |

Restricted articles never become public summaries. In newsletters and on public lists they appear only with a summary an editor wrote on purpose: the excerpt, the subtitle or an issue summary. Check those before publishing.

## Publishing directly

In Blog and Blog + newsletter modes, people with **Publish articles** can publish their own posts straight from the editor and edit them afterwards, without a review. Everyone else still submits for review. Publishing a post doesn't send it to subscribers; add it to an issue for that. See [blog and newsletter modes](publication-modes.md).

## Deleting articles

To delete an article from the dashboard, first unpublish or decline it. Deleting can't be undone.
