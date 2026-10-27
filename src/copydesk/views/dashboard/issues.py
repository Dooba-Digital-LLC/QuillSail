"""Putting issues together, previewing, testing, scheduling and sending them."""

from __future__ import annotations

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from copydesk import audit, issue_previews, newsletter, settings
from copydesk import permissions as perms
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import get_or_404, json_object, paginate
from copydesk.models import Article, AuditEntry, Delivery, Group, Issue, IssueItem, IssuePreview
from copydesk.security import too_many
from copydesk.utils import (
    is_valid_email,
    local_to_utc,
    parse_datetime_local,
    parse_int,
    parse_lines,
    utcnow,
)
from copydesk.views.dashboard import bp


def _issue(issue_id: int) -> Issue:
    return get_or_404(Issue, issue_id)


def _editable(issue: Issue):
    if not issue.is_editable:
        flash("This issue has already gone out, so it can't be changed.", "error")
        return redirect(url_for("dash.issue_report", issue_id=issue.id))
    return None


def _audience_groups() -> list[Group]:
    return list(db.session.scalars(select(Group).where(Group.is_audience.is_(True)).order_by(Group.position)).all())


@bp.get("/issues/")
@perms.require("manage_issues")
def issues():
    page = paginate(select(Issue).order_by(Issue.number.desc()), per_page=25)
    stats = {issue.id: newsletter.delivery_stats(issue) for issue in page.items if issue.status in ("sending", "sent")}
    return render_template("dashboard/issues/list.html", page=page, stats=stats, dash_active="issues")


@bp.post("/issues/new")
@perms.require("manage_issues")
def issue_new():
    issue = newsletter.create(current_user)
    db.session.commit()
    flash(f"Issue #{issue.number} created. Give it a title, write an introduction and add some articles.", "success")
    return redirect(url_for("dash.issue", issue_id=issue.id))


@bp.route("/issues/<int:issue_id>/", methods=["GET", "POST"])
@perms.require("manage_issues")
def issue(issue_id: int):
    item = _issue(issue_id)
    if not item.is_editable and request.method == "GET":
        return redirect(url_for("dash.issue_report", issue_id=item.id))
    errors: dict[str, str] = {}
    if request.method == "POST":
        blocked = _editable(item)
        if blocked:
            return blocked
        form = request.form
        number = parse_int(form.get("number"))
        if number is None or number < 1:
            errors["number"] = "Enter a whole number, like 12."
        elif db.session.scalar(select(Issue.id).where(Issue.number == number, Issue.id != item.id)):
            errors["number"] = f"There's already an issue #{number}."
        audience_mode = form.get("audience_mode", "all")
        wanted = {parse_int(v) for v in form.getlist("audience_groups")}
        chosen = [grp for grp in _audience_groups() if grp.id in wanted]
        if audience_mode == "groups" and not chosen:
            errors["audience_groups"] = "Pick at least one group, or send to everyone."
        if not errors:
            item.number = number
            item.title = (form.get("title") or "").strip()[:200]
            item.subject = (form.get("subject") or "").strip()[:255]
            item.preheader = (form.get("preheader") or "").strip()[:255]
            item.intro = form.get("intro", "").replace("\r\n", "\n")
            item.audience_mode = "groups" if audience_mode == "groups" else "all"
            item.audience_groups = chosen if item.audience_mode == "groups" else []
            item.publish_articles = bool(form.get("publish_articles"))
            item.updated_at = utcnow()
            audit.record("issue.updated", item, actor=current_user)
            db.session.commit()
            flash("Issue saved.", "success")
            return redirect(url_for("dash.issue", issue_id=item.id))
    candidates = newsletter.eligible_articles(item)
    return render_template(
        "dashboard/issues/edit.html",
        issue=item,
        errors=errors,
        form=request.form if request.method == "POST" else None,
        candidates=candidates,
        candidate_issues=newsletter.issues_containing([a.id for a in candidates]),
        audience_groups=_audience_groups(),
        recipient_count=len(newsletter.recipients(item)),
        default_subject=newsletter.default_subject(item),
        problems=newsletter.problems(item),
        dash_active="issues",
    ), (422 if errors else 200)


@bp.post("/issues/<int:issue_id>/items")
@perms.require("manage_issues")
def issue_item_add(issue_id: int):
    item = _issue(issue_id)
    blocked = _editable(item)
    if blocked:
        return blocked
    kind = request.form.get("kind", "article")
    if kind == "article":
        article = db.session.get(Article, parse_int(request.form.get("article_id")))
        if article is None or article.status not in ("accepted", "published"):
            flash("Pick an accepted or published article.", "error")
            return redirect(url_for("dash.issue", issue_id=item.id))
        newsletter.add_article(item, article)
        message = f"Added “{article.display_title}”."
    else:
        if kind not in ("text", "heading"):
            abort(400)
        title = request.form.get("title", "")
        if kind == "heading" and not title.strip():
            flash("A section heading needs some text.", "error")
            return redirect(url_for("dash.issue", issue_id=item.id))
        newsletter.add_block(item, kind, title=title, body=request.form.get("body", ""))
        message = "Heading added." if kind == "heading" else "Text block added. Open it below to write it."
    item.updated_at = utcnow()
    db.session.commit()
    flash(message, "success")
    return redirect(url_for("dash.issue", issue_id=item.id) + "#items")


def _issue_item(issue: Issue, item_id: int) -> IssueItem:
    entry = db.session.get(IssueItem, item_id)
    if entry is None or entry.issue_id != issue.id:
        abort(404)
    return entry


@bp.post("/issues/<int:issue_id>/items/<int:item_id>")
@perms.require("manage_issues")
def issue_item_update(issue_id: int, item_id: int):
    item = _issue(issue_id)
    blocked = _editable(item)
    if blocked:
        return blocked
    entry = _issue_item(item, item_id)
    form = request.form
    action = form.get("action", "save")
    if action in ("up", "down"):
        newsletter.move(item, entry, -1 if action == "up" else 1)
    elif action == "remove":
        item.items.remove(entry)
        newsletter.renumber(item)
        flash("Removed from the issue.", "success")
    else:
        entry.title = (form.get("title") or "").strip()[:200]
        entry.body = form.get("body", "").replace("\r\n", "\n")
        if entry.kind == "article":
            entry.display = "full" if form.get("display") == "full" else "summary"
        wanted = {parse_int(v) for v in form.getlist("item_groups")}
        entry.audience_groups = [grp for grp in _audience_groups() if grp.id in wanted]
        flash("Item saved.", "success")
    item.updated_at = utcnow()
    db.session.commit()
    anchor = "#items" if action == "remove" else f"#item-{entry.id}"
    return redirect(url_for("dash.issue", issue_id=item.id) + anchor)


@bp.post("/issues/<int:issue_id>/reorder")
@perms.require("manage_issues")
def issue_reorder(issue_id: int):
    item = _issue(issue_id)
    if not item.is_editable:
        return jsonify(message="This issue has already gone out."), 409
    order = json_object().get("order")
    if not isinstance(order, list) or any(not str(i).isdigit() for i in order):
        abort(400, description="Order must be a list of item IDs.")
    ids = [int(i) for i in order]
    if len(ids) != len(set(ids)) or set(ids) != {entry.id for entry in item.items}:
        abort(409, description="The items changed. Reload the issue before reordering it.")
    newsletter.reorder(item, ids)
    db.session.commit()
    return jsonify(ok=True)


def _preview_audience(issue: Issue):
    """Read ?audience= : "all" (every item), "none" (people in no group) or a group slug."""
    choice = request.args.get("audience", "all")
    if choice == "all":
        return None, "Everything (every group's items)"
    if choice == "none":
        return set(), "Subscribers who aren't in any group"
    group = db.session.scalar(select(Group).where(Group.slug == choice))
    if group is None:
        return None, "Everything (every group's items)"
    return {group.id}, group.name


@bp.get("/issues/<int:issue_id>/preview")
@perms.require("manage_issues")
def issue_preview(issue_id: int):
    item = _issue(issue_id)
    audience, label = _preview_audience(item)
    subject, _html, text = newsletter.render(item, audience_ids=audience)
    return render_template(
        "dashboard/issues/preview.html",
        issue=item,
        audience=request.args.get("audience", "all"),
        audience_label=label,
        audience_groups=_audience_groups(),
        subject=subject,
        text_version=text,
        shared_link=db.session.scalar(select(IssuePreview).where(IssuePreview.issue_id == item.id)),
        dash_active="issues",
    )


@bp.post("/issues/<int:issue_id>/share")
@perms.require("manage_issues")
def issue_share(issue_id: int):
    item = _issue(issue_id)
    if request.form.get("action") == "revoke":
        issue_previews.revoke(item, current_user)
        db.session.commit()
        flash("Private preview link revoked.", "success")
        return redirect(url_for("dash.issue_preview", issue_id=item.id))
    try:
        url = issue_previews.create(item, current_user, request.form.get("slug", ""),
                                    parse_int(request.form.get("days"), 30) or 30)
    except newsletter.IssueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dash.issue_preview", issue_id=item.id))
    db.session.commit()
    return render_template("dashboard/issues/shared.html", issue=item, share_url=url, dash_active="issues")


@bp.get("/issues/<int:issue_id>/email")
@perms.require("manage_issues")
def issue_email(issue_id: int):
    """The email's HTML on its own, shown inside the preview page's frame."""
    item = _issue(issue_id)
    audience, _label = _preview_audience(item)
    _subject, html, _text = newsletter.render(item, audience_ids=audience)
    g.csp_override = "default-src 'none'; img-src * data:; style-src 'unsafe-inline'; frame-ancestors 'self'"
    g.allow_framing = True
    return html


@bp.post("/issues/<int:issue_id>/test")
@perms.require("manage_issues")
def issue_test(issue_id: int):
    item = _issue(issue_id)
    addresses = [a.lower() for a in parse_lines(request.form.get("addresses", ""))] or [current_user.email]
    bad = [a for a in addresses if not is_valid_email(a)]
    if bad:
        flash(f"These don't look like email addresses: {', '.join(bad)}", "error")
    elif too_many("issue-test", 20, 3600, key=str(current_user.id)):
        flash("You've sent a lot of tests in the last hour. Please wait a bit.", "warning")
    else:
        audience, label = _preview_audience(item)
        newsletter.send_test(item, addresses, audience, current_user)
        db.session.commit()
        flash(f"Test sent to {', '.join(addresses[:10])} ({label.lower()}). It should arrive within a minute.", "success")
    return redirect(url_for("dash.issue_preview", issue_id=item.id, audience=request.args.get("audience", "all")))


@bp.route("/issues/<int:issue_id>/send", methods=["GET", "POST"])
@perms.require("send_issues")
def issue_send(issue_id: int):
    item = _issue(issue_id)
    blocked = _editable(item)
    if blocked:
        return blocked
    tz = settings.get("site.timezone")
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "schedule":
                when = parse_datetime_local(request.form.get("scheduled_at"))
                if when is None:
                    raise newsletter.IssueError("Pick a date and time to send it.")
                newsletter.schedule(item, local_to_utc(when, tz), current_user)
                db.session.commit()
                flash(f"Scheduled. Issue #{item.number} will go out on {request.form.get('scheduled_at').replace('T', ' at ')}.", "success")
                return redirect(url_for("dash.issues"))
            if action == "send":
                if request.form.get("confirm_number") != str(item.number):
                    raise newsletter.IssueError(f"To confirm, type the issue number ({item.number}).")
                newsletter.start_sending(item, current_user)
                db.session.commit()
                flash(f"Issue #{item.number} is on its way.", "success")
                return redirect(url_for("dash.issue_report", issue_id=item.id))
        except newsletter.IssueError as exc:
            flash(str(exc), "error")
    recipients = newsletter.recipients(item)
    tested = db.session.scalar(
        select(func.count()).where(
            AuditEntry.action == "issue.test_sent",
            AuditEntry.target_type == "issue",
            AuditEntry.target_id == item.id,
        )
    )
    return render_template(
        "dashboard/issues/send.html",
        issue=item,
        recipients=recipients,
        recipient_count=len(recipients),
        problems=newsletter.problems(item),
        subject=newsletter.subject_for(item),
        audience_label=newsletter.audience_label(item),
        tested=bool(tested),
        dash_active="issues",
    )


@bp.post("/issues/<int:issue_id>/unschedule")
@perms.require("send_issues")
def issue_unschedule(issue_id: int):
    item = _issue(issue_id)
    newsletter.unschedule(item, current_user)
    db.session.commit()
    flash("No longer scheduled. The issue is a draft again.", "success")
    return redirect(url_for("dash.issue", issue_id=item.id))


@bp.get("/issues/<int:issue_id>/report")
@perms.require("manage_issues")
def issue_report(issue_id: int):
    item = _issue(issue_id)
    stats = newsletter.delivery_stats(item)
    failures = db.session.scalars(
        select(Delivery).where(Delivery.issue_id == item.id, Delivery.status == "failed").order_by(Delivery.id).limit(100)
    ).all()
    return render_template(
        "dashboard/issues/report.html",
        issue=item,
        stats=stats,
        failures=failures,
        audience_label=newsletter.audience_label(item),
        dash_active="issues",
    )


@bp.post("/issues/<int:issue_id>/retry")
@perms.require("send_issues")
def issue_retry(issue_id: int):
    item = _issue(issue_id)
    count = newsletter.retry_failed(item, current_user)
    db.session.commit()
    flash(f"Trying {count} failed emails again." if count else "There are no failed emails to retry.", "success")
    return redirect(url_for("dash.issue_report", issue_id=item.id))


@bp.post("/issues/<int:issue_id>/publish")
@perms.require("manage_issues")
def issue_publish(issue_id: int):
    item = _issue(issue_id)
    if item.is_public:
        flash("This issue is already on the website.", "warning")
    else:
        newsletter.publish_web(item, current_user)
        db.session.commit()
        flash(f"Issue #{item.number} is on the website. No emails were sent.", "success")
    return redirect(url_for("dash.issue", issue_id=item.id) if item.is_editable else url_for("dash.issue_report", issue_id=item.id))


@bp.post("/issues/<int:issue_id>/delete")
@perms.require("manage_issues")
def issue_delete(issue_id: int):
    item = _issue(issue_id)
    if not item.is_editable or item.is_public:
        flash("Issues that were sent or published can't be deleted; they're part of the archive.", "error")
        return redirect(url_for("dash.issues"))
    number = item.number
    audit.record("issue.deleted", item, actor=current_user)
    db.session.delete(item)
    db.session.commit()
    flash(f"Issue #{number} was deleted. Its articles weren't affected.", "success")
    return redirect(url_for("dash.issues"))
