"""The private business inbox, follow-up notes, and editorial handoff."""

from __future__ import annotations

from urllib.parse import quote

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from copydesk import audit, business, settings
from copydesk import permissions as perms
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import get_or_404, paginate
from copydesk.models import BusinessInquiry
from copydesk.utils import utcnow
from copydesk.views.dashboard import bp


@bp.route("/business/", methods=["GET", "POST"])
@perms.require("manage_business")
def businesses():
    if request.method == "POST":
        if not perms.has(current_user, "manage_settings"):
            abort(403)
        settings.set_value("business.enabled", bool(request.form.get("enabled")))
        audit.record("settings.updated", label="Business outreach", actor=current_user)
        db.session.commit()
        return redirect(url_for("dash.businesses"))
    status = request.args.get("status", "")
    query = select(BusinessInquiry).order_by(BusinessInquiry.created_at.desc())
    if status in dict(business.STATUSES) or status == "unverified":
        query = query.where(BusinessInquiry.status == status)
    return render_template("dashboard/business/list.html", page=paginate(query, per_page=25),
                           statuses=business.STATUSES, status=status, kinds=dict(business.KINDS),
                           enabled=settings.get("business.enabled"), dash_active="businesses")


@bp.route("/business/<int:inquiry_id>/", methods=["GET", "POST"])
@perms.require("manage_business")
def business_inquiry(inquiry_id: int):
    item = get_or_404(BusinessInquiry, inquiry_id)
    errors = {}
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            audit.record("business.deleted", item, actor=current_user, label=f"Business inquiry #{item.id}")
            db.session.delete(item)
            db.session.commit()
            flash("Business inquiry deleted.", "success")
            return redirect(url_for("dash.businesses"))
        if action == "draft":
            if not perms.has(current_user, "submit"):
                abort(403)
            try:
                article = business.create_article(item, current_user)
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                db.session.commit()
                flash("Editorial draft created. Review it before accepting it into an issue.", "success")
                return redirect(url_for("write.edit", article_id=article.id))
        elif action == "save":
            status = request.form.get("status", "")
            notes = (request.form.get("staff_notes") or "").strip()
            if status not in dict(business.STATUSES) and status != "unverified":
                errors["status"] = "Choose a valid status."
            elif item.verified_at is None and status not in ("unverified", "closed"):
                errors["status"] = "The contact must confirm their email before review or approval."
            elif item.verified_at is not None and status == "unverified":
                errors["status"] = "This contact has already confirmed their email."
            if len(notes) > 12000:
                errors["staff_notes"] = "Use 12,000 characters or fewer."
            if not errors:
                item.status, item.staff_notes, item.updated_at = status, notes, utcnow()
                audit.record("business.updated", item, actor=current_user, label=f"Business inquiry #{item.id}")
                db.session.commit()
                flash("Follow-up saved.", "success")
                return redirect(url_for("dash.business_inquiry", inquiry_id=item.id))
        else:
            abort(400)
    reply_url = f"mailto:{quote(item.email, safe='@.')}?subject={quote('Re: ' + item.title, safe='')}"
    return render_template("dashboard/business/detail.html", inquiry=item, errors=errors,
                           form=request.form, statuses=business.STATUSES, kinds=dict(business.KINDS),
                           reply_url=reply_url, dash_active="businesses"), (422 if errors else 200)
