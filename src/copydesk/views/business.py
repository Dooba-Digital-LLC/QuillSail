"""A public, moderated way for businesses to reach the publication's staff."""

from __future__ import annotations

import hashlib

from flask import Blueprint, abort, redirect, render_template, request, url_for

from copydesk import business, settings
from copydesk import permissions as perms
from copydesk.auth import current_user, read_token
from copydesk.db import db
from copydesk.models import BusinessInquiry
from copydesk.security import too_many
from copydesk.utils import normalize_email

bp = Blueprint("business", __name__, url_prefix="/business")


@bp.route("/", methods=["GET", "POST"])
def contact():
    if not settings.get("business.enabled") and not perms.has(current_user, "manage_business"):
        abort(404)
    errors = {}
    if request.method == "POST":
        if request.form.get("website"):
            return redirect(url_for("business.thanks"))
        values, errors = business.validate(request.form)
        if not errors:
            email_key = hashlib.sha256(normalize_email(values["email"]).encode()).hexdigest()
            if too_many("business-contact", 5, 3600) or too_many("business-email", 3, 86400, key=email_key):
                abort(429)
            inquiry = BusinessInquiry(**values)
            db.session.add(inquiry)
            db.session.flush()
            business.request_confirmation(inquiry)
            db.session.commit()
            return redirect(url_for("business.thanks"))
    return render_template("business/contact.html", errors=errors, form=request.form,
                           kinds=business.KINDS, active_nav="business"), (422 if errors else 200)


@bp.get("/thanks")
def thanks():
    return render_template("business/thanks.html", confirmed=request.args.get("confirmed") == "1")


@bp.route("/confirm/<token>", methods=["GET", "POST"])
def confirm(token: str):
    data = read_token("confirm-business", token)
    inquiry = db.session.get(BusinessInquiry, data.get("id")) if data else None
    if inquiry is None or inquiry.email != data.get("email"):
        abort(404)
    if request.method == "POST":
        business.confirm(inquiry)
        db.session.commit()
        return redirect(url_for("business.thanks", confirmed="1"))
    return render_template("business/confirm.html", inquiry=inquiry)
