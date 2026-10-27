"""The activity log: who did what, and when."""

from __future__ import annotations

from flask import render_template, request
from sqlalchemy import select

from copydesk import audit
from copydesk import permissions as perms
from copydesk.helpers import paginate
from copydesk.models import AuditEntry, User
from copydesk.views.dashboard import bp

AREAS = [
    ("", "Everything"),
    ("article", "Articles"),
    ("issue", "Issues"),
    ("subscriber", "Subscribers"),
    ("user", "Accounts"),
    ("membership", "Group membership"),
    ("group", "Groups and permissions"),
    ("settings", "Settings"),
    ("page", "Pages"),
    ("section", "Sections"),
]


@bp.get("/log/")
@perms.require("view_log")
def log():
    area = request.args.get("area", "")
    who = (request.args.get("who") or "").strip().lower()
    stmt = select(AuditEntry).order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
    if area:
        stmt = stmt.where(AuditEntry.action.like(f"{area}.%"))
    if who:
        stmt = stmt.join(User, User.id == AuditEntry.actor_id).where(User.username == who)
    return render_template(
        "dashboard/log.html",
        page=paginate(stmt, per_page=50),
        areas=AREAS,
        area=area,
        who=who,
        describe=audit.describe,
        dash_active="log",
    )
