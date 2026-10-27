"""The subscriber list: browse, add, edit, import and export."""

from __future__ import annotations

import csv
import io

from flask import Response, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select

from copydesk import audit
from copydesk import permissions as perms
from copydesk import subscribers as subs
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import get_or_404, paginate
from copydesk.models import Group, Subscriber, subscriber_groups
from copydesk.utils import is_valid_email, normalize_email, parse_int, utcnow
from copydesk.views.dashboard import bp

STATUS_LABELS = {
    "active": "Subscribed",
    "pending": "Not confirmed",
    "unsubscribed": "Unsubscribed",
    "bounced": "Bouncing",
}
MAX_IMPORT_ROWS = 20_000


def _groups() -> list[Group]:
    return list(db.session.scalars(select(Group).where(Group.is_audience.is_(True)).order_by(Group.position)).all())


def _filtered():
    status = request.args.get("status", "active")
    q = (request.args.get("q") or "").strip().lower()
    group_id = parse_int(request.args.get("group"))
    stmt = select(Subscriber)
    if status in STATUS_LABELS:
        stmt = stmt.where(Subscriber.status == status)
    if q:
        stmt = stmt.where(or_(func.lower(Subscriber.email).like(f"%{q}%"), func.lower(Subscriber.name).like(f"%{q}%")))
    if group_id:
        stmt = stmt.where(
            Subscriber.id.in_(select(subscriber_groups.c.subscriber_id).where(subscriber_groups.c.group_id == group_id))
        )
    return stmt, status, q, group_id


@bp.get("/subscribers/")
@perms.require("manage_subscribers")
def subscribers():
    stmt, status, q, group_id = _filtered()
    counts = dict(db.session.execute(select(Subscriber.status, func.count()).group_by(Subscriber.status)).all())
    group_counts = dict(db.session.execute(
        select(subscriber_groups.c.group_id, func.count())
        .join(Subscriber, Subscriber.id == subscriber_groups.c.subscriber_id)
        .where(Subscriber.status == "active")
        .group_by(subscriber_groups.c.group_id)
    ).all())
    return render_template(
        "dashboard/subscribers/list.html",
        page=paginate(stmt.order_by(Subscriber.created_at.desc()), per_page=50),
        status=status,
        q=q,
        group_id=group_id,
        counts=counts,
        group_counts=group_counts,
        groups=_groups(),
        labels=STATUS_LABELS,
        dash_active="subscribers",
    )


@bp.route("/subscribers/new", methods=["GET", "POST"])
@perms.require("manage_subscribers")
def subscriber_new():
    errors: dict[str, str] = {}
    form = request.form
    if request.method == "POST":
        email = normalize_email(form.get("email"))
        if not is_valid_email(email):
            errors["email"] = "Enter a valid email address."
        elif subs.find(email) is not None:
            errors["email"] = "That address is already on the list."
        consent = bool(form.get("consent"))
        if not errors:
            wanted = {parse_int(v) for v in form.getlist("groups")}
            chosen = [g for g in _groups() if g.id in wanted]
            subscriber, outcome = subs.subscribe(
                email, name=(form.get("name") or "").strip(), groups=chosen, source="admin", confirmed=consent
            )
            audit.record("subscriber.added", subscriber, actor=current_user)
            db.session.commit()
            if outcome == "active":
                flash(f"{email} is subscribed.", "success")
            else:
                flash(f"We sent {email} an email to confirm. They'll be subscribed once they click it.", "success")
            return redirect(url_for("dash.subscribers"))
    return render_template("dashboard/subscribers/new.html", errors=errors, form=form, groups=_groups(),
                           dash_active="subscribers"), (422 if errors else 200)


@bp.route("/subscribers/<int:subscriber_id>/", methods=["GET", "POST"])
@perms.require("manage_subscribers")
def subscriber(subscriber_id: int):
    item = get_or_404(Subscriber, subscriber_id)
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            email = item.email
            audit.record("subscriber.deleted", item, actor=current_user)
            db.session.delete(item)
            db.session.commit()
            flash(f"{email} was removed from the list for good.", "success")
            return redirect(url_for("dash.subscribers"))
        if action == "unsubscribe":
            subs.unsubscribe(item)
        elif action == "resubscribe":
            subs.activate(item, welcome=False)
        else:
            wanted = {parse_int(v) for v in request.form.getlist("groups")}
            item.groups = [g for g in _groups() if g.id in wanted]
            item.name = (request.form.get("name") or "").strip()[:100]
        audit.record("subscriber.updated", item, actor=current_user, details={"action": action})
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("dash.subscriber", subscriber_id=item.id))
    from copydesk.models import Delivery, Issue

    deliveries = db.session.execute(
        select(Delivery, Issue).join(Issue, Issue.id == Delivery.issue_id)
        .where(Delivery.subscriber_id == item.id).order_by(Delivery.id.desc()).limit(20)
    ).all()
    return render_template("dashboard/subscribers/edit.html", subscriber=item, groups=_groups(),
                           deliveries=deliveries, labels=STATUS_LABELS, dash_active="subscribers")


@bp.route("/subscribers/import", methods=["GET", "POST"])
@perms.require("manage_subscribers")
def subscriber_import():
    result = None
    error = None
    if request.method == "POST":
        upload = request.files.get("file")
        mode = request.form.get("mode", "confirm")
        if upload is None or not upload.filename:
            error = "Choose a CSV file to import."
        elif mode == "consented" and not request.form.get("consent"):
            error = "Tick the box to confirm these people agreed to get the newsletter, or send them a confirmation email instead."
        else:
            try:
                text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                text = None
                error = "That file isn't plain-text CSV (UTF-8). Export it from your spreadsheet as “CSV UTF-8” and try again."
            if text is not None:
                result = import_csv(text, confirmed=mode == "consented")
                if result["error"]:
                    error = result["error"]
                else:
                    audit.record("subscriber.imported", None, actor=current_user, label=upload.filename,
                                 details={k: v for k, v in result.items() if k != "problems"})
                    db.session.commit()
    return render_template("dashboard/subscribers/import.html", result=result, error=error, groups=_groups(),
                           dash_active="subscribers")


def import_csv(text: str, *, confirmed: bool) -> dict:
    """Add subscribers from CSV text with an "email" column (and optional "name" and "groups")."""
    reader = csv.DictReader(io.StringIO(text))
    fields = {name.strip().lower(): name for name in (reader.fieldnames or [])}
    result = {"added": 0, "existing": 0, "invalid": 0, "problems": [], "error": None}
    if "email" not in fields:
        result["error"] = "The file needs a column headed “email”. Optional columns: “name” and “groups”."
        return result
    groups = {g.slug: g for g in _groups()}
    groups.update({g.name.lower(): g for g in groups.values()})
    seen: set[str] = set()
    for line_number, row in enumerate(reader, start=2):
        if line_number > MAX_IMPORT_ROWS + 1:
            result["problems"].append(f"Stopped after {MAX_IMPORT_ROWS:,} rows. Split the file and import the rest.")
            break
        email = normalize_email(row.get(fields["email"]))
        if not email:
            continue
        if not is_valid_email(email):
            result["invalid"] += 1
            if len(result["problems"]) < 25:
                result["problems"].append(f"Row {line_number}: “{email}” isn't a valid email address.")
            continue
        if email in seen or subs.find(email) is not None:
            result["existing"] += 1
            seen.add(email)
            continue
        seen.add(email)
        names = [n.strip().lower() for n in (row.get(fields.get("groups", ""), "") or "").replace(";", ",").split(",")]
        chosen = [groups[n] for n in names if n in groups]
        subs.subscribe(
            email,
            name=(row.get(fields.get("name", ""), "") or "").strip()[:100],
            groups=chosen,
            source="import",
            confirmed=confirmed,
            welcome=False,
        )
        result["added"] += 1
        if result["added"] % 500 == 0:
            db.session.flush()
    return result


@bp.get("/subscribers/export.csv")
@perms.require("manage_subscribers")
def subscriber_export():
    stmt, status, _q, _group = _filtered()
    rows = db.session.scalars(stmt.order_by(Subscriber.created_at)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "name", "status", "groups", "subscribed_at", "confirmed_at", "source"])
    for sub in rows:
        writer.writerow([
            sub.email, sub.name, sub.status, ", ".join(g.slug for g in sub.groups),
            sub.created_at.isoformat(timespec="seconds"),
            sub.confirmed_at.isoformat(timespec="seconds") if sub.confirmed_at else "",
            sub.source,
        ])
    audit.record("subscriber.exported", None, actor=current_user, label=f"{len(rows)} rows ({status})")
    db.session.commit()
    response = Response(buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="subscribers-{status}-{utcnow():%Y-%m-%d}.csv"'
    return response
