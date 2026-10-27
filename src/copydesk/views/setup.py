"""First run: create the first administrator from the browser.

Normally `quillsail init` does this on the command line. When QuillSail starts
with no accounts (in Docker, say), every page redirects here. To stop a
stranger from claiming the site first, the form asks for a one-time setup
code, which is written to the server log and to data/setup-code.txt.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import os
import secrets

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from copydesk import audit, publication, seed, settings
from copydesk.auth import hash_password, login_user, password_problem, suggest_username
from copydesk.db import db
from copydesk.helpers import state
from copydesk.models import ADMINS, Group, GroupMembership, User
from copydesk.utils import is_valid_email, normalize_email, utcnow

bp = Blueprint("setup", __name__, url_prefix="/setup")
log = logging.getLogger("copydesk.setup")
CODE_KEY = "system.setup_code"


def _code_file():
    return state().config.data_dir / "setup-code.txt"


def setup_code() -> str:
    code = settings.get(CODE_KEY, None)
    if not code:
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        settings.set_value(CODE_KEY, code)
        db.session.commit()
        try:
            path = _code_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
        except OSError:
            pass
        log.warning("QuillSail setup code: %s (open %s/setup/ to finish installing)", code, state().config.base_url)
    return code


def _has_users() -> bool:
    return db.session.scalar(select(User.id).limit(1)) is not None


@bp.route("/", methods=["GET", "POST"])
def index():
    if _has_users():
        state().setup_needed = False
        return redirect(url_for("public.home"))
    desktop = current_app.extensions.get("copydesk_desktop")
    desktop_setup = desktop is not None and desktop.setup_authorized()
    code = None if desktop_setup else setup_code()
    form = request.form
    errors: dict[str, str] = {}
    if request.method == "POST":
        if not desktop_setup and not hmac.compare_digest((form.get("code") or "").strip().upper(), code):
            errors["code"] = "That code doesn't match. Find it in the server log or in data/setup-code.txt."
        site_name = (form.get("site_name") or "").strip()
        mode = form.get("site_mode", "newsletter")
        if mode not in publication.MODES:
            errors["site_mode"] = "Choose Newsletter, Blog, or Blog + newsletter."
        if not site_name:
            errors["site_name"] = "Enter the publication's name."
        name = (form.get("display_name") or "").strip()
        if not name:
            errors["display_name"] = "Enter your name."
        email = normalize_email(form.get("email"))
        if not is_valid_email(email):
            errors["email"] = "Enter a valid email address."
        problem = password_problem(form.get("password") or "", email)
        if problem:
            errors["password"] = problem
        if not errors:
            seed.seed(site_name)
            settings.set_value("site.name", site_name)
            settings.set_value("site.mode", mode)
            user = create_admin(email=email, name=name, password=form.get("password") or "")
            settings.set_value(CODE_KEY, "")
            audit.record("setup.completed", None, actor=user, label=site_name)
            db.session.commit()
            with contextlib.suppress(OSError):
                _code_file().unlink()
            state().setup_needed = False
            login_user(user, remember=True)
            db.session.commit()
            flash(f"Welcome to {site_name}! Here's your dashboard. The checklist below walks you through the rest.", "success")
            return redirect(url_for("dash.overview"))
    return render_template("setup/index.html", form=form, errors=errors, desktop_setup=desktop_setup), (422 if errors else 200)


def create_admin(*, email: str, name: str, password: str) -> User:
    """Create a confirmed administrator account (used by setup and `quillsail init`)."""
    user = User(
        email=email,
        username=suggest_username(name, email),
        display_name=name,
        password_hash=hash_password(password),
        email_verified_at=utcnow(),
    )
    db.session.add(user)
    db.session.flush()
    for slug in (ADMINS, "editors"):
        group = db.session.scalar(select(Group).where(Group.slug == slug))
        if group is not None:
            db.session.add(GroupMembership(user=user, group=group, status="active", source="admin"))
    return user
