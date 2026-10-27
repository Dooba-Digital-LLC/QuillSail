"""Sample content for trying QuillSail out: `quillsail demo`.

Creates fictional writers in each reader group, articles at every stage of
the editorial workflow, and one sent issue, so you can click around before
real content exists. Everyone and everything here is made up. Run it only
on a fresh test site.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from copydesk import newsletter, subscribers, workflow
from copydesk.auth import hash_password
from copydesk.db import db
from copydesk.models import (
    Article,
    ArticleAuthor,
    ArticleEvent,
    ArticleRevision,
    Delivery,
    Group,
    GroupMembership,
    Section,
    User,
)
from copydesk.utils import utcnow, word_count

PEOPLE = [
    ("maya-patel", "Maya Patel", "students", {"major": "Computer Science", "graduation_year": 2027},
     "Junior studying computer science. Breaks things on purpose, then writes about fixing them."),
    ("leo-nguyen", "Leo Nguyen", "students", {"major": "Computer Engineering", "graduation_year": 2026},
     "Embedded systems nerd and coffee-powered hackathon regular."),
    ("jordan-lee", "Dr. Jordan Lee", "staff", {"department": "Computer Science and Engineering", "role": "Lecturer"},
     "Teaches systems programming and still uses a paper notebook."),
    ("sam-rivera", "Sam Rivera", "alumni", {"graduation_year": 2019, "employer": "A cloud infrastructure company"},
     "Site reliability engineer. Happy to talk about on-call, outages and career switches."),
]

ARTICLES = [
    {
        "author": "maya-patel", "section": "tutorials", "status": "published", "featured": True, "days": 3,
        "title": "Git branches, explained with sticky notes",
        "subtitle": "The mental model that finally made merging click for me.",
        "body": (
            "If `git merge` makes your palms sweat, you're not alone. For my first two years of college I "
            "treated Git like a spell book: copy the incantation, hope nothing explodes.\n\n"
            "What finally helped was thinking of every commit as a **sticky note** and every branch as a "
            "**finger pointing at one note**.\n\n"
            "## A branch is just a pointer\n\n"
            "When you run `git branch feature`, Git doesn't copy your code. It sticks a new finger on the "
            "note you're looking at. That's why branches are so cheap to make.\n\n"
            "```bash\ngit switch -c feature/search\n# ...make some commits...\ngit switch main\n"
            "git merge feature/search\n```\n\n"
            "::: tip Try this\nRun `git log --oneline --graph --all` before and after a merge. Watching the "
            "fingers move is the whole trick.\n:::\n\n"
            "## Merge versus rebase\n\n"
            "A merge adds a new note that has *two* parents. A rebase peels your notes off and sticks them "
            "back on top of someone else's. Same final code, different history.\n\n"
            "| | Merge | Rebase |\n|---|---|---|\n| History | Shows exactly what happened | Looks like a "
            "straight line |\n| Safe on shared branches? | Yes | Only if nobody else has the commits |\n\n"
            "Once the sticky-note picture is in your head, the scary commands stop being scary. Happy "
            "branching!"
        ),
    },
    {
        "author": "sam-rivera", "section": "features", "status": "published", "days": 5,
        "title": "What I wish I'd known before my first on-call shift",
        "subtitle": "An alum on pagers, panic and the power of writing things down.",
        "body": (
            "Five years ago I graduated, joined an infrastructure team, and was handed a pager in my second "
            "week. Here's what I'd tell my younger self.\n\n"
            "## The alert is not the emergency\n\n"
            "The first time my phone buzzed at 2 a.m., I tried to fix everything at once. The best on-call "
            "engineers do the opposite: they slow down, read the alert twice and write down what they see.\n\n"
            "> Calm is a skill, and like any skill it gets better with practice.\n\n"
            "## Runbooks are love letters to future you\n\n"
            "Every incident I've handled well had a runbook behind it. Every one I handled badly ended with "
            "me writing one.\n\n"
            "If you're a student reading this: join the teams that write things down. It's the best career "
            "advice I've got."
        ),
    },
    {
        "author": "jordan-lee", "section": "opinion", "status": "published", "days": 9,
        "title": "In defense of reading the manual",
        "subtitle": "Search engines and assistants are great. The man pages are better than you think.",
        "body": (
            "Every semester, someone in my systems class discovers `man 2 open` and looks at me like I've "
            "shown them a secret door.\n\n"
            "Documentation written by the people who built a tool tells you *why* it works the way it does, "
            "not just *what* to type. That's the difference between copying a fix and understanding one.\n\n"
            "So here's my small challenge for this month: the next time you reach for a search engine, open "
            "the manual first. Give it five minutes. You might be surprised."
        ),
    },
    {
        "author": "leo-nguyen", "section": "community", "status": "published", "days": 12,
        "title": "Recap: 24 hours, 31 teams and one very tired soldering iron",
        "subtitle": "Highlights from this fall's hackathon.",
        "body": (
            "This year's hackathon was our biggest yet: 31 teams, three tracks and far too much pizza.\n\n"
            "- **Best hardware hack** went to a plant-watering robot that tweets when it's thirsty.\n"
            "- **Best first-time team** built an accessible campus map in a single night.\n"
            "- **Most chaotic demo** belonged to a game controlled entirely by clapping.\n\n"
            "Thank you to every volunteer, mentor and judge. See you in the spring!"
        ),
    },
    {
        "author": "maya-patel", "section": "features", "status": "submitted", "days": 1,
        "title": "How I set up my first home lab for under $100",
        "subtitle": "Old laptops, a cheap switch and a lot of curiosity.",
        "body": (
            "A home lab sounds expensive, but mine started with a laptop from a recycling bin and a $20 "
            "network switch. Here's the whole setup, what it cost, and what I learned running my own "
            "services for a semester.\n\nFirst, the hardware: an old ThinkPad, a used mini PC and a "
            "Raspberry Pi I won at a workshop."
        ),
    },
    {
        "author": "leo-nguyen", "section": "tutorials", "status": "changes_requested", "days": 4,
        "title": "Debugging embedded C with nothing but an LED",
        "subtitle": "Blink codes, logic analyzers and patience.",
        "body": (
            "When your microcontroller doesn't have a screen, an LED is your best friend. In this tutorial "
            "I'll show you how I use blink codes to find out exactly where my firmware gets stuck, and when "
            "it's time to reach for a logic analyzer instead."
        ),
        "feedback": "Love this idea! Could you add a short code sample showing the blink-code helper, and a "
                    "sentence on which boards this works with? Then it's ready to go.",
    },
    {
        "author": "sam-rivera", "section": "community", "status": "accepted", "days": 2,
        "title": "Alumni office hours are back",
        "subtitle": "Graduates are volunteering their time to talk careers, interviews and grad school.",
        "body": (
            "Starting next month, alumni volunteers will hold virtual office hours every other Thursday. "
            "Bring your résumé, your interview nerves or your questions about grad school. No question is "
            "too small, and you don't need to sign up in advance."
        ),
    },
]


def run() -> dict[str, int]:
    if db.session.scalar(select(Article.id).limit(1)) is not None:
        raise RuntimeError("This site already has articles. The demo only runs on an empty site.")
    now = utcnow()
    groups = {g.slug: g for g in db.session.scalars(select(Group)).all()}
    sections = {s.slug: s for s in db.session.scalars(select(Section)).all()}
    editor = db.session.scalar(
        select(User).join(GroupMembership, GroupMembership.user_id == User.id)
        .join(Group, Group.id == GroupMembership.group_id).where(Group.slug == "editors")
    )
    people: dict[str, User] = {}
    for username, name, group_slug, data, bio in PEOPLE:
        user = User(
            email=f"{username}@example.edu", username=username, display_name=name, bio=bio,
            password_hash=hash_password("demo-password-123"), email_verified_at=now - timedelta(days=30),
            created_at=now - timedelta(days=30),
        )
        db.session.add(user)
        db.session.flush()
        if group_slug in groups:
            db.session.add(GroupMembership(user=user, group=groups[group_slug], status="active", source="admin", data=data))
        people[username] = user
        subscribers.subscribe(user.email, name=name, groups=[groups[group_slug]] if group_slug in groups else [],
                              source="account", user=user, confirmed=True, welcome=False)
    pending_user = User(
        email="riley-chen@example.edu", username="riley-chen", display_name="Riley Chen",
        password_hash=hash_password("demo-password-123"), email_verified_at=now - timedelta(days=1),
    )
    db.session.add(pending_user)
    db.session.flush()
    if "alumni" in groups:
        db.session.add(GroupMembership(user=pending_user, group=groups["alumni"], status="pending", source="self",
                                       data={"graduation_year": 2021}, note="I graduated in 2021 and helped run the robotics club."))

    created: list[Article] = []
    for spec in ARTICLES:
        author = people[spec["author"]]
        when = now - timedelta(days=spec["days"])
        article = Article(
            title=spec["title"], subtitle=spec["subtitle"], body=spec["body"],
            slug=workflow.unique_slug(spec["title"]), status=spec["status"],
            section_id=sections[spec["section"]].id if spec["section"] in sections else None,
            featured=spec.get("featured", False), word_count=word_count(spec["body"]),
            created_by_id=author.id, created_at=when - timedelta(days=2), updated_at=when,
            submitted_at=when - timedelta(days=1),
            published_at=when if spec["status"] == "published" else None,
            assigned_editor_id=editor.id if editor and spec["status"] != "submitted" else None,
        )
        article.author_links.append(ArticleAuthor(user_id=author.id, position=0))
        db.session.add(article)
        db.session.flush()
        db.session.add(ArticleRevision(article_id=article.id, user_id=author.id, title=article.title,
                                       subtitle=article.subtitle, body=article.body, word_count=article.word_count,
                                       created_at=when - timedelta(days=1), summary="First draft"))
        db.session.add(ArticleEvent(article_id=article.id, user_id=author.id, kind="status", from_status="draft",
                                    to_status="submitted", created_at=when - timedelta(days=1)))
        if spec.get("feedback") and editor:
            db.session.add(ArticleEvent(article_id=article.id, user_id=editor.id, kind="status", from_status="in_review",
                                        to_status="changes_requested", body=spec["feedback"], created_at=when))
        elif spec["status"] in ("accepted", "published") and editor:
            db.session.add(ArticleEvent(article_id=article.id, user_id=editor.id, kind="status", from_status="in_review",
                                        to_status="accepted", body="Great piece. Thank you!",
                                        created_at=when - timedelta(hours=6)))
        created.append(article)

    if editor:
        issue = newsletter.create(editor, "Branches, pagers and manuals")
        issue.intro = ("Welcome to the first issue! This week: a gentle guide to Git branches, an alum's survival "
                       "guide to on-call, and a staff case for reading the manual.\n\nGot something to say? "
                       "Anyone in our community can write for us.")
        issue.preheader = "Git branches with sticky notes, surviving on-call, and more."
        for article in created:
            if article.status == "published" and article.title != "Recap: 24 hours, 31 teams and one very tired soldering iron":
                newsletter.add_article(issue, article)
        heading = newsletter.add_block(issue, "heading", title="For alumni")
        block = newsletter.add_block(issue, "text", title="Come back and mentor",
                                     body="Alumni office hours return next month. Reply to this email if you'd "
                                          "like to volunteer an hour.")
        if "alumni" in groups:
            heading.audience_groups = [groups["alumni"]]
            block.audience_groups = [groups["alumni"]]
        issue.status = "sent"
        issue.send_started_at = issue.sent_at = issue.published_at = now - timedelta(days=2)
        db.session.flush()
        for sub in newsletter.recipients(issue):
            db.session.add(Delivery(issue_id=issue.id, subscriber_id=sub.id, email=sub.email, status="sent",
                                    sent_at=now - timedelta(days=2), attempts=1))
        issue.recipient_count = len(newsletter.recipients(issue))
        draft = newsletter.create(editor, "Home labs and hackathons")
        for article in created:
            if article.status == "accepted":
                newsletter.add_article(draft, article)
    db.session.commit()
    return {"people": len(people) + 1, "articles": len(created)}
