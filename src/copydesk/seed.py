"""What a new site starts with: groups, sections, starter pages and settings.

``seed()`` is safe to run more than once. It only adds what's missing and
never overwrites anything an editor has changed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from copydesk import settings
from copydesk.db import db
from copydesk.models import ADMINS, EDITORS, EVERYONE, MEMBERS, Group, Page, Section

DEFAULT_GROUPS: list[dict[str, Any]] = [
    {
        "slug": EVERYONE, "name": "Everyone", "is_system": True, "is_implicit": True,
        "description": "Anyone visiting the site, signed in or not.",
        "permissions": ["read"], "position": 0, "show_badge": False, "on_signup_form": False,
    },
    {
        "slug": MEMBERS, "name": "Members", "member_label": "Member", "is_system": True, "is_implicit": True,
        "description": "Everyone who has an account and has confirmed their email address.",
        "permissions": ["read_members", "submit"], "position": 1, "show_badge": False, "on_signup_form": False,
    },
    {
        "slug": EDITORS, "name": "Editors", "member_label": "Editor", "is_system": True, "color": "teal",
        "description": "Review submissions, edit and publish articles, put issues together and send them.",
        "permissions": ["review", "edit_any", "publish", "manage_issues", "send_issues",
                        "manage_subscribers", "manage_pages", "view_log"],
        "position": 2, "show_badge": True, "on_signup_form": False,
    },
    {
        "slug": ADMINS, "name": "Administrators", "member_label": "Administrator", "is_system": True,
        "color": "red", "description": "Full access, including settings, groups and permissions.",
        "permissions": [], "position": 3, "show_badge": False, "on_signup_form": False,
    },
    {
        "slug": "students", "name": "Students", "member_label": "Student", "is_audience": True,
        "description": "Currently enrolled students.", "color": "blue", "join_policy": "approval",
        "byline_template": "{major}", "position": 10,
        "fields": [
            {"key": "major", "label": "Major", "type": "text", "required": False, "public": True},
            {"key": "graduation_year", "label": "Expected graduation year", "type": "number",
             "required": False, "public": True},
        ],
    },
    {
        "slug": "staff", "name": "Staff", "member_label": "Staff", "is_audience": True,
        "description": "Faculty and staff.", "color": "green", "join_policy": "approval",
        "byline_template": "{department}", "position": 11,
        "fields": [
            {"key": "department", "label": "Department", "type": "text", "required": False, "public": True},
            {"key": "role", "label": "Role or title", "type": "text", "required": False, "public": True},
        ],
    },
    {
        "slug": "alumni", "name": "Alumni", "member_label": "Alum", "is_audience": True,
        "description": "Graduates who want to stay in touch.", "color": "purple", "join_policy": "approval",
        "byline_template": "Class of {graduation_year}", "position": 12,
        "fields": [
            {"key": "graduation_year", "label": "Graduation year", "type": "number", "required": True, "public": True},
            {"key": "employer", "label": "Where you work now", "type": "text", "required": False, "public": True},
        ],
    },
]

DEFAULT_SECTIONS = [
    ("news", "News", "What's happening in our community."),
    ("features", "Features", "Longer stories, profiles and deep dives."),
    ("tutorials", "Tutorials", "Step-by-step guides and how-tos."),
    ("opinion", "Opinion", "Arguments, essays and hot takes."),
    ("community", "Community", "Events, shout-outs and member news."),
]

DEFAULT_PAGES = [
    {
        "slug": "about",
        "title": "About",
        "show_in_nav": True,
        "nav_position": 1,
        "body": (
            "**Edit this page** in the dashboard under *Pages*. Tell readers who you are, what you "
            "publish and how often, and who's behind it.\n\n"
            "## Who writes here\n\n"
            "Our readers do! Anyone in our community can [submit a piece](write/). Editors read every "
            "submission, work with writers on changes, and publish the best on the site and in the newsletter.\n"
        ),
    },
    {
        "slug": "guidelines",
        "title": "Writing guidelines",
        "show_in_nav": False,
        "nav_position": 2,
        "body": (
            "These starter guidelines are here to edit. Make them yours under *Pages* in the dashboard.\n\n"
            "## What we publish\n\n"
            "- **News**: something that happened, or is about to, that our readers should know about.\n"
            "- **Features**: profiles, stories and deep dives.\n"
            "- **Tutorials**: teach something you know, step by step.\n"
            "- **Opinion**: make an argument and back it up.\n\n"
            "## Before you submit\n\n"
            "1. **Lead with the point.** Readers decide in the first two sentences whether to keep going.\n"
            "2. **Keep it readable.** Short paragraphs, plain words, and headings for anything long.\n"
            "3. **Credit your sources.** Link to what you quote or build on.\n"
            "4. **Add alt text** to images so everyone can follow along.\n"
            "5. **Be kind.** Critique ideas, not people.\n\n"
            "## What happens next\n\n"
            "An editor reads every submission. They may ask for changes, and you'll get an email at each "
            "step. Once your piece is accepted it goes on the site, and often into the next issue of the "
            "newsletter too.\n\n"
            "## Formatting\n\n"
            "The editor uses Markdown: `**bold**`, `*italic*`, `## Heading`, `[link](https://…)`, "
            "`- list item`, and code blocks fenced with three backticks. Use `::: tip` … `:::` for a "
            "highlighted box.\n"
        ),
    },
    {
        "slug": "privacy",
        "title": "Privacy",
        "show_in_nav": False,
        "nav_position": 3,
        "body": (
            "*This is a starting point. Check it with your organization before relying on it.*\n\n"
            "## What we store\n\n"
            "- **Subscribers:** your email address, your name if you gave it, the groups you picked, "
            "and when you subscribed or unsubscribed.\n"
            "- **Members:** your account details, profile, group memberships, and anything you write "
            "or comment here.\n"
            "- **Email delivery:** whether each issue was sent to you successfully. We don't track "
            "opens or clicks.\n\n"
            "## What we don't do\n\n"
            "We don't sell or share your information, and we don't use advertising trackers.\n\n"
            "## Your choices\n\n"
            "Every newsletter has a one-click unsubscribe link. Members can download, edit or delete "
            "their account from their account settings. Questions? Contact the editors.\n"
        ),
    },
]


def seed(site_name: str | None = None, *, extra_settings: dict[str, Any] | None = None) -> None:
    existing_groups = {g.slug for g in db.session.scalars(select(Group)).all()}
    for spec in DEFAULT_GROUPS:
        if spec["slug"] not in existing_groups:
            db.session.add(Group(**spec))

    if not db.session.scalar(select(Section.id).limit(1)):
        for position, (slug, name, description) in enumerate(DEFAULT_SECTIONS):
            db.session.add(Section(slug=slug, name=name, description=description, position=position))

    existing_pages = {p.slug for p in db.session.scalars(select(Page)).all()}
    for spec in DEFAULT_PAGES:
        if spec["slug"] not in existing_pages:
            db.session.add(Page(**spec))

    if site_name and settings.get("site.name") == settings.REGISTRY["site.name"].default:
        settings.set_value("site.name", site_name)
    for key, value in (extra_settings or {}).items():
        settings.set_value(key, value)
    db.session.commit()
