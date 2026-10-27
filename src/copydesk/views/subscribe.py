"""Newsletter sign-up, confirmation, preferences and unsubscribing.

These pages work for anyone, with or without an account. Links in emails
carry a secret token per subscriber, so nobody has to sign in to manage or
cancel their subscription.
"""

from __future__ import annotations

import hmac

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select

from copydesk import notify, settings, subscribers
from copydesk.auth import current_user, read_token
from copydesk.db import db
from copydesk.models import Subscriber
from copydesk.security import csrf_exempt, too_many
from copydesk.utils import is_valid_email, normalize_email

bp = Blueprint("subscribe", __name__)


def offered_groups():
    return subscribers.signup_groups() if settings.get("newsletter.ask_audience") else []


def _trusted_form() -> bool:
    """A signed-in member submitting our own form (with a valid CSRF token)."""
    token = request.form.get("csrf_token", "")
    expected = session.get("_csrf", "")
    return bool(token and expected and hmac.compare_digest(token, expected))


@bp.route("/subscribe", methods=["GET", "POST"])
@csrf_exempt  # sign-up forms may be embedded on other sites; double opt-in protects the list
def subscribe():
    audience = offered_groups()
    error = None
    email = normalize_email(request.form.get("email"))
    if request.method == "POST":
        if request.form.get("website"):
            return redirect(url_for("subscribe.thanks"))
        if not is_valid_email(email):
            error = "Enter a valid email address, like name@example.com."
        elif too_many("subscribe", 10, 3600):
            error = "Too many sign-ups from here in the last hour. Please try again later."
        else:
            wanted = set(request.form.getlist("groups"))
            chosen = [g for g in audience if g.slug in wanted]
            trusted = (
                current_user.is_authenticated
                and current_user.is_verified
                and current_user.email == email
                and _trusted_form()
            )
            subscriber, outcome = subscribers.subscribe(
                email,
                name=(request.form.get("name") or "").strip()[:100],
                groups=chosen,
                source="form",
                user=current_user if trusted else None,
                confirmed=trusted,
            )
            if outcome == "already" and not trusted:
                notify.send(
                    "already_subscribed", subscriber.email, f"You're already subscribed to {notify.site_name()}",
                    subscriber=subscriber, **notify.subscriber_links(subscriber),
                )
            db.session.commit()
            return redirect(url_for("subscribe.thanks", done="1" if trusted else None))
    status = 422 if error else 200
    return render_template("subscribe/form.html", groups=audience, error=error, email=email), status


@bp.get("/subscribe/thanks")
def thanks():
    return render_template("subscribe/thanks.html", done=request.args.get("done") == "1")


@bp.get("/subscribe/confirm/<token>")
def confirm(token: str):
    data = read_token("confirm-subscription", token)
    subscriber = db.session.get(Subscriber, data.get("sid")) if data else None
    if subscriber is None or subscriber.email != data.get("email") or subscriber.status not in ("pending", "active"):
        return render_template("auth/link_problem.html", kind="subscription"), 400
    subscribers.activate(subscriber)
    db.session.commit()
    return render_template("subscribe/confirmed.html", subscriber=subscriber)


def _by_token(token: str) -> Subscriber:
    subscriber = db.session.scalar(select(Subscriber).where(Subscriber.token == token))
    if subscriber is None:
        abort(404)
    return subscriber


@bp.route("/subscription/<token>", methods=["GET", "POST"])
@csrf_exempt  # authorized by the secret token in the link
def manage(token: str):
    subscriber = _by_token(token)
    audience = subscribers.audience_groups()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "unsubscribe":
            subscribers.unsubscribe(subscriber)
            db.session.commit()
            return render_template("subscribe/unsubscribed.html", subscriber=subscriber)
        if action == "resubscribe":
            subscribers.activate(subscriber, welcome=False)
            db.session.commit()
            flash("Welcome back! You're subscribed again.", "success")
            return redirect(url_for("subscribe.manage", token=token))
        wanted = set(request.form.getlist("groups"))
        offered = [g for g in audience if g.on_signup_form or g in subscriber.groups]
        subscriber.groups = [g for g in offered if g.slug in wanted]
        subscriber.name = (request.form.get("name") or "").strip()[:100]
        db.session.commit()
        flash("Your preferences have been saved.", "success")
        return redirect(url_for("subscribe.manage", token=token))
    offered = [g for g in audience if g.on_signup_form or g in subscriber.groups]
    return render_template("subscribe/manage.html", subscriber=subscriber, groups=offered)


@bp.route("/unsubscribe/<token>", methods=["GET", "POST"])
@csrf_exempt  # authorized by the token; also receives RFC 8058 one-click POSTs from mail apps
def unsubscribe(token: str):
    subscriber = _by_token(token)
    if request.method == "POST":
        subscribers.unsubscribe(subscriber)
        db.session.commit()
        if request.form.get("List-Unsubscribe") == "One-Click":
            return "Unsubscribed.", 200, {"Content-Type": "text/plain; charset=utf-8"}
        return render_template("subscribe/unsubscribed.html", subscriber=subscriber)
    return render_template("subscribe/unsubscribe.html", subscriber=subscriber)
