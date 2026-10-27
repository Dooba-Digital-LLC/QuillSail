"""Signing in and out, the current user, passwords and emailed tokens."""

from __future__ import annotations

from functools import wraps

from flask import current_app, g, redirect, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from werkzeug.local import LocalProxy
from werkzeug.security import check_password_hash, generate_password_hash

from copydesk.db import db
from copydesk.models import AnonymousUser, User
from copydesk.utils import USERNAME_RE, slugify, utcnow

ANONYMOUS = AnonymousUser()

RESERVED_USERNAMES = frozenset(
    {"admin", "administrator", "editor", "editors", "staff", "support", "help", "root",
     "system", "copydesk", "dashboard", "api", "account", "me", "new", "settings"}
)

# A short list of passwords people pick most often. Anything longer than the
# 10-character minimum that is on this list is still refused.
COMMON_PASSWORDS = frozenset(
    {"1234567890", "12345678910", "0123456789", "qwertyuiop", "password12", "password123",
     "password1234", "iloveyou12", "qwerty1234", "1q2w3e4r5t", "abcdefghij", "letmein123",
     "welcome123", "admin12345", "passw0rd12", "football12", "baseball12", "sunshine12",
     "princess12", "changeme12", "1111111111", "aaaaaaaaaa", "zaq12wsxcde"}
)

TOKEN_MAX_AGE = {
    "confirm-business": 3 * 24 * 3600,
    "verify-email": 3 * 24 * 3600,
    "reset-password": 2 * 3600,
    "change-email": 2 * 24 * 3600,
    "confirm-subscription": 7 * 24 * 3600,
    "invite": 7 * 24 * 3600,
}


def _get_user():
    return g.get("user", ANONYMOUS)


current_user = LocalProxy(_get_user)


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def check_password(user: User, password: str) -> bool:
    return bool(user.password_hash) and check_password_hash(user.password_hash, password)


def password_problem(password: str, email: str = "") -> str | None:
    """Explain what's wrong with a new password, or return None if it's fine."""
    if len(password) < 10:
        return "Use at least 10 characters. A few random words work well."
    if len(password) > 256:
        return "That's longer than we can store. Use 256 characters or fewer."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(password)) < 4:
        return "That password is too easy to guess. Try a few random words instead."
    if email and lowered in (email.lower(), email.split("@")[0].lower()):
        return "Don't use your email address as your password."
    return None


def verify_user(user: User) -> bool:
    """Apply all verification effects, including when an invitation sets a password."""
    if user.is_verified:
        return False
    from copydesk import audit, groups, subscribers
    from copydesk.helpers import state

    user.email_verified_at = utcnow()
    subscribers.link_account(user)
    subscriber = subscribers.for_user(user)
    if subscriber is not None and subscriber.status == "pending" and subscriber.user_id == user.id:
        subscribers.activate(subscriber)
    groups.after_verification(user)
    audit.record("user.verified", user, actor=user)
    state().hooks.fire("user.verified", user=user)
    return True


def login_user(user: User, remember: bool = False) -> None:
    csrf = session.get("_csrf")
    session.clear()
    if csrf:
        session["_csrf"] = csrf
    session["uid"] = user.id
    session["uv"] = user.auth_version
    session.permanent = remember
    user.last_seen_at = utcnow()
    g.user = user


def logout_user() -> None:
    session.clear()
    g.user = ANONYMOUS


def load_user() -> None:
    """Runs before every request."""
    g.user = ANONYMOUS
    uid = session.get("uid")
    if not uid:
        return
    user = db.session.get(User, uid)
    if (
        user is None
        or not user.is_active
        or user.is_deleted
        or session.get("uv") != user.auth_version
    ):
        session.pop("uid", None)
        session.pop("uv", None)
        return
    g.user = user
    now = utcnow()
    if user.last_seen_at is None or (now - user.last_seen_at).total_seconds() > 900:
        user.last_seen_at = now
        db.session.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def verified_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if not current_user.is_verified:
            return redirect(url_for("auth.verify_notice"))
        return view(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------- tokens


def _serializer(purpose: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=f"copydesk.{purpose}")


def make_token(purpose: str, data: dict) -> str:
    return _serializer(purpose).dumps(data)


def read_token(purpose: str, token: str) -> dict | None:
    try:
        data = _serializer(purpose).loads(token, max_age=TOKEN_MAX_AGE.get(purpose, 3600))
    except (SignatureExpired, BadSignature):
        return None
    return data if isinstance(data, dict) else None


def reset_token(user: User) -> str:
    # Including part of the password hash makes the link stop working once it's used.
    return make_token("reset-password", {"uid": user.id, "ph": user.password_hash[-16:]})


def user_from_reset_token(token: str) -> User | None:
    """The user a password-reset (or invitation) link belongs to, if it's still valid."""
    data = read_token("reset-password", token) or read_token("invite", token)
    if not data:
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or user.is_deleted or user.password_hash[-16:] != data.get("ph"):
        return None
    return user


# --------------------------------------------------------------------------- usernames


def username_problem(username: str, *, exclude_user_id: int | None = None) -> str | None:
    if not USERNAME_RE.match(username):
        return "Use 3–40 lowercase letters, numbers and hyphens (not at the start or end)."
    if username in RESERVED_USERNAMES:
        return "That username is reserved. Please pick another."
    query = select(User.id).where(func.lower(User.username) == username.lower())
    if exclude_user_id:
        query = query.where(User.id != exclude_user_id)
    if db.session.scalar(query):
        return "Someone already has that username."
    return None


def suggest_username(name: str, email: str = "") -> str:
    base = slugify(name, 30, "") or slugify(email.split("@")[0], 30, "") or "member"
    if len(base) < 3:
        base = f"{base}-writer".strip("-")
    candidate = base
    number = 2
    while username_problem(candidate) is not None:
        candidate = f"{base}-{number}"
        number += 1
        if number > 999:
            candidate = f"member-{utcnow():%y%m%d%H%M%S}"
            break
    return candidate
