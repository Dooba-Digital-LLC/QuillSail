"""Creating an account, signing in and out, confirming email, resetting passwords."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from copydesk import audit, groups, notify, settings, subscribers
from copydesk.auth import (
    check_password,
    current_user,
    hash_password,
    login_required,
    login_user,
    logout_user,
    password_problem,
    read_token,
    suggest_username,
    user_from_reset_token,
    verify_user,
)
from copydesk.db import db
from copydesk.helpers import next_url, state
from copydesk.models import Group, Page, User
from copydesk.security import too_many
from copydesk.utils import domain_matches, is_valid_email, normalize_email

bp = Blueprint("auth", __name__)


def registration_state() -> tuple[bool, str]:
    """(open?, explanation) for the sign-up page."""
    mode = settings.get("accounts.registration")
    if mode == "closed":
        return False, "New accounts are created by the editors. Contact them if you'd like to take part."
    if mode == "domains":
        domains = ", ".join("@" + d for d in settings.get("accounts.allowed_domains") or [])
        return True, f"You'll need an email address ending in {domains}." if domains else ""
    return True, ""


def terms_page() -> Page | None:
    slug = settings.get("accounts.terms_page")
    if not slug:
        return None
    return db.session.scalar(select(Page).where(Page.slug == slug, Page.is_published.is_(True)))


@bp.route("/join", methods=["GET", "POST"])
def join():
    if current_user.is_authenticated:
        return redirect(url_for("write.index"))
    is_open, note = registration_state()
    audience = groups.joinable_groups() if settings.get("accounts.ask_audience") else []
    terms = terms_page()
    form = request.form
    errors: dict[str, str] = {}

    if request.method == "POST" and is_open:
        if form.get("website"):
            return redirect(url_for("auth.verify_notice"))  # a bot filled the hidden field
        if too_many("join", 5, 3600):
            flash("Too many accounts were created from here recently. Please try again in an hour.", "error")
            return render_template("auth/join.html", form=form, errors={}, audience=audience, terms=terms,
                                   is_open=is_open, note=note, groups_api=groups,
                                   field_values=submitted_field_values(audience, form)), 429
        name = (form.get("display_name") or "").strip()
        email = normalize_email(form.get("email"))
        password = form.get("password") or ""
        if not name:
            errors["display_name"] = "Enter your name as you'd like it to appear on your writing."
        elif len(name) > 100:
            errors["display_name"] = "Keep your name under 100 characters."
        if not is_valid_email(email):
            errors["email"] = "Enter a valid email address, like name@example.com."
        elif db.session.scalar(select(User.id).where(func.lower(User.email) == email)):
            errors["email"] = "There's already an account with this email. Try signing in instead."
        elif settings.get("accounts.registration") == "domains" and not domain_matches(
            email, settings.get("accounts.allowed_domains") or []
        ):
            errors["email"] = note or "Accounts are limited to certain email domains."
        problem = password_problem(password, email)
        if problem:
            errors["password"] = problem
        if terms and not form.get("agree"):
            errors["agree"] = f"Please read and agree to the {terms.title.lower()} to continue."
        chosen = [g for g in audience if g.slug in form.getlist("groups")]
        field_data: dict[str, dict] = {}
        for group in chosen:
            data, field_errors = groups.parse_fields(group, form)
            field_data[group.slug] = data
            errors.update(field_errors)

        if not errors:
            user = User(
                email=email,
                username=suggest_username(name, email),
                display_name=name,
                password_hash=hash_password(password),
            )
            db.session.add(user)
            db.session.flush()
            for group in chosen:
                try:
                    groups.request(user, group, data=field_data.get(group.slug))
                except groups.GroupError:
                    continue
            if form.get("subscribe"):
                subscribers.subscribe(email, name=name, groups=chosen, source="account", user=user,
                                      send_confirmation=False)
            audit.record("user.registered", user, actor=user)
            notify.verify_email(user)
            db.session.commit()
            state().hooks.fire("user.registered", user=user)
            login_user(user)
            db.session.commit()
            return redirect(url_for("auth.verify_notice"))

    status = 422 if errors else 200
    return render_template("auth/join.html", form=form, errors=errors, audience=audience, terms=terms,
                           is_open=is_open, note=note, groups_api=groups,
                           field_values=submitted_field_values(audience, form)), status


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(next_url(url_for("public.home")))
    error = None
    email = normalize_email(request.form.get("email"))
    if request.method == "POST":
        if too_many("login-ip", 20, 900) or (email and too_many("login-email", 8, 900, key=email)):
            error = "Too many sign-in attempts. Please wait 15 minutes and try again."
        else:
            user = db.session.scalar(select(User).where(func.lower(User.email) == email))
            if user is None or user.is_deleted or not check_password(user, request.form.get("password") or ""):
                error = "That email and password don't match. Check them and try again."
            elif not user.is_active:
                error = "This account has been blocked. If you think that's a mistake, contact the editors."
            else:
                login_user(user, remember=bool(request.form.get("remember")))
                db.session.commit()
                flash(f"Welcome back, {user.display_name}.", "success")
                if not user.is_verified:
                    return redirect(url_for("auth.verify_notice"))
                return redirect(next_url(url_for("public.home")))
    return render_template("auth/login.html", error=error, email=email), (401 if error else 200)


@bp.post("/logout")
def logout():
    logout_user()
    flash("You've been signed out.", "success")
    return redirect(url_for("public.home"))


@bp.get("/verify-email")
@login_required
def verify_notice():
    if current_user.is_verified:
        return redirect(url_for("write.index"))
    return render_template("auth/verify_notice.html")


@bp.post("/verify-email/resend")
@login_required
def resend_verification():
    if current_user.is_verified:
        return redirect(url_for("write.index"))
    if too_many("resend-verify", 3, 3600, key=str(current_user.id)):
        flash("We've already sent a few emails. Check your spam folder, or try again in an hour.", "warning")
    else:
        notify.verify_email(current_user)
        db.session.commit()
        flash(f"We sent a new confirmation link to {current_user.email}.", "success")
    return redirect(url_for("auth.verify_notice"))


@bp.get("/verify-email/<token>")
def verify_email(token: str):
    data = read_token("verify-email", token)
    user = db.session.get(User, data.get("uid")) if data else None
    if user is None or user.is_deleted or not user.is_active or user.email != data.get("email"):
        return render_template("auth/link_problem.html", kind="verify"), 400
    if verify_user(user):
        db.session.commit()
    flash("Your email address is confirmed. You're all set.", "success")
    if current_user.is_authenticated and current_user.id == user.id:
        pending = [m for m in user.memberships if m.status == "pending"]
        return redirect(url_for("account.groups") if pending else url_for("write.index"))
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = False
    email = normalize_email(request.form.get("email"))
    error = None
    if request.method == "POST":
        if not is_valid_email(email):
            error = "Enter the email address you signed up with."
        elif too_many("forgot-ip", 10, 3600) or too_many("forgot-email", 3, 3600, key=email):
            sent = True  # don't reveal anything; just stop sending
        else:
            user = db.session.scalar(select(User).where(func.lower(User.email) == email))
            if user is not None and user.is_active and not user.is_deleted:
                notify.password_reset(user)
                db.session.commit()
            sent = True
    return render_template("auth/forgot.html", sent=sent, email=email, error=error)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    user = user_from_reset_token(token)
    if user is None or not user.is_active:
        return render_template("auth/link_problem.html", kind="reset"), 400
    error = None
    if request.method == "POST":
        password = request.form.get("password") or ""
        error = password_problem(password, user.email)
        if not error and password != request.form.get("confirm"):
            error = "The two passwords don't match."
        if not error:
            user.password_hash = hash_password(password)
            user.auth_version += 1
            verify_user(user)
            audit.record("user.password_changed", user, actor=user)
            notify.password_changed(user)
            login_user(user)
            db.session.commit()
            flash("Your password has been changed and you're signed in.", "success")
            return redirect(url_for("public.home"))
    return render_template("auth/reset.html", error=error, user=user, token=token)


def submitted_field_values(audience: list[Group], form) -> dict[str, dict[str, str]]:
    """Custom-field values typed into the sign-up form, keyed by group then field."""
    return {
        g.slug: {spec["key"]: form.get(f"field-{g.slug}-{spec['key']}", "") for spec in g.fields or []}
        for g in audience
    }
