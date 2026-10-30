# Groups and permissions

In QuillSail, groups are both roles and kinds of readers. **Editors** is a group; so is **Alumni**. Permissions belong to groups, and each person has every permission their groups allow. Manage both under **Dashboard → Groups and permissions**.

## The groups every site has

| Group | Who's in it | Notes |
| --- | --- | --- |
| **Everyone** | Every visitor, signed in or not | Implicit: nobody joins it. It can read the public site. |
| **Members** | Everyone with a confirmed account | Implicit. Members can write, submit and read members-only articles. |
| **Editors** | People you add | Review, edit, publish, put issues together and send them, manage subscribers and pages, and read the activity log. |
| **Administrators** | People you add | Always hold every permission, so the site can't lock itself out. |

These four can't be deleted, and their short names don't change. Their permissions can be adjusted, except that administrators always have everything. Accounts that haven't confirmed their email address get only the **Everyone** permissions.

## Reader groups

Reader groups describe who your readers are: **Students**, **Staff** and **Alumni** to start with. Rename, change or delete them, or add your own. Each reader group has:

- **A joining rule.** *Open* groups let people join straight away. *Approval* groups create a request that someone with **Manage members** approves or declines under **Dashboard → Members**. *Closed* groups are joined only when an administrator adds someone.
- **Email domains** (optional). Anyone who confirms an address at one of these domains joins automatically, skipping approval. Subdomains are included. Use this only for domains that reliably identify the group, such as a university's student domain.
- **Profile fields**, such as *Major* or *Graduation year*. Fields can be required, and can be shown publicly.
- **A badge and a byline.** A badge shows the group on a member's profile and bylines. The byline template turns profile fields into a line under the writer's name, such as `Class of {graduation_year}`.
- **A place on the sign-up forms.** Offered groups appear when someone creates an account or subscribes to the newsletter.

Members manage their own groups under **Account → Groups**: they can ask to join, update their profile fields, withdraw a request, or leave. The only administrator can't leave the Administrators group until someone else is made an administrator.

> [!IMPORTANT]
> When someone subscribes to the newsletter, the groups they pick are *preferences* that decide which parts of issues they get. They don't make that person a member of the group and grant no permissions. Never rely on newsletter audience choices to protect confidential material.

## Permissions

| Area | Permission | Lets people |
| --- | --- | --- |
| Reading | Read the publication | See published public articles, issues and pages |
| Reading | Read members-only articles | See articles limited to signed-in members |
| Writing | Write and submit | Write drafts and send them to the editors |
| Editing | Review submissions | Use the submission queue: feedback, change requests, accept or decline |
| Editing | Edit any article | Change the text of anyone's article once it's submitted |
| Editing | Publish articles | Publish articles, or take them down |
| Newsletter | Put issues together | Create issues and choose what goes in them |
| Newsletter | Send issues | Send or schedule issues |
| Newsletter | Manage subscribers | See, add, import, export and remove subscribers |
| People | Manage members | Edit accounts, approve requests to join groups, block people |
| People | Manage groups and permissions | Create groups and decide what each can do |
| People | Manage business outreach | Read business inquiries and approve opportunities |
| Site | Manage pages and sections | Edit pages such as About, and the sections |
| Site | Change site settings | Change the name, look, email and sign-up settings |
| Site | See the activity log | See who did what, and when |
| Competitions | Host competitions | Create competitions, moderate entries, appoint judges, publish results |

Extensions can add permissions of their own. They appear in the same editor. The [permissions reference](reference/permissions.md) is generated from the code and always matches your version.

The permissions overview under **Dashboard → Groups and permissions** shows every group against every permission on one page.

## Nobody can give themselves more power

QuillSail enforces one rule everywhere: **you can grant only the permissions you hold.** Someone with **Manage groups and permissions** but not **Send issues** can't give any group **Send issues**. Someone with **Manage members** can't put people in a group whose permissions exceed their own, or change the joining rules of such a group. Administrator membership can't be gained by joining or by an email domain rule.

Permission checks happen on the server for every page and action. Hiding a button is never the only protection.

## Recipes

**A student publication with student editors.** Keep **Students** as an approval group with your university's student email domain. Create a **Student editors** group with *Review submissions*, *Edit any article* and *Put issues together*, but not *Send issues*. A staff advisor in **Editors** sends each issue.

**A blog with a few trusted authors.** Create an **Authors** group with *Publish articles*. Authors can then publish their own posts directly from the editor, while other members still submit for review. See [blog and newsletter modes](publication-modes.md).

**Members-only writing.** In an article's publishing settings, an editor can limit it to signed-in members or to particular reader groups. In emails, restricted articles appear only as a title and summary, and QuillSail never builds that summary from the restricted text. See [writing and review](writing-and-review.md#who-can-read-a-published-article).
