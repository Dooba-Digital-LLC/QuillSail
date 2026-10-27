"""Emails QuillSail sends to people (as opposed to newsletter issues).

Every message is rendered from a pair of templates, ``email/<name>.html`` and
``email/<name>.txt``, which themes can override. It is then queued as a job
and sent by the worker.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import render_template
from sqlalchemy import select

from copydesk import jobs, permissions, settings
from copydesk.auth import make_token, reset_token
from copydesk.db import db
from copydesk.email_connections import effective_mail_config
from copydesk.helpers import external_url, state
from copydesk.mail import Email, Mailer, MailError
from copydesk.models import ADMINS, Article, ArticleEvent, Group, GroupMembership, Subscriber, User

log = logging.getLogger("copydesk.notify")

EMAIL_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# Inline styles for email templates (available there as ``es``). Email apps
# ignore most <style> blocks, so every element carries its own styles.
EMAIL_STYLE = {
    "font": EMAIL_FONT,
    "h1": f"margin:0 0 16px;font-family:{EMAIL_FONT};font-size:24px;line-height:1.3;font-weight:700;color:#16181d;",
    "h2": f"margin:0 0 8px;font-family:{EMAIL_FONT};font-size:20px;line-height:1.35;font-weight:700;color:#16181d;",
    "p": f"margin:0 0 16px;font-family:{EMAIL_FONT};font-size:16px;line-height:1.6;color:#16181d;",
    "small": f"margin:0 0 12px;font-family:{EMAIL_FONT};font-size:13px;line-height:1.5;color:#5b616e;",
    "quote": f"margin:0 0 16px;padding:12px 16px;background:#f5f6f8;border-left:3px solid #c9ced8;"
             f"border-radius:4px;font-family:{EMAIL_FONT};font-size:15px;line-height:1.6;color:#2b2f36;",
    "label": f"margin:0 0 6px;font-family:{EMAIL_FONT};font-size:12px;font-weight:700;letter-spacing:0.08em;"
             f"text-transform:uppercase;color:#5b616e;",
}


def site_name() -> str:
    return settings.get("site.name")


def reply_to_address() -> str:
    return settings.get("newsletter.reply_to") or settings.get("site.contact_email") or ""


def compose(template: str, to: str, subject: str, *, category: str = "notification", **context: Any) -> Email:
    context.setdefault("email_subject", subject)
    html = render_template(f"email/{template}.html", **context)
    text = render_template(f"email/{template}.txt", **context)
    headers = {"Auto-Submitted": "auto-generated"} if category == "notification" else {}
    return Email(
        to=to,
        subject=subject,
        text=text.strip() + "\n",
        html=html,
        from_name=site_name(),
        from_email=effective_mail_config().from_email,
        reply_to=reply_to_address(),
        headers=headers,
        category=category,
    )


def queue(email: Email, priority: int = jobs.PRIORITY_URGENT) -> None:
    jobs.enqueue("mail.send", email.to_payload(), priority=priority, max_attempts=6)


def send(template: str, to: str, subject: str, **context: Any) -> None:
    queue(compose(template, to, subject, **context))


@jobs.handler("mail.send")
def _send_job(payload: dict[str, Any]) -> None:
    email = Email.from_payload(payload)
    email = state().hooks.apply_filters("mail.before_send", email)
    if email is None:
        return
    with Mailer() as mailer:
        try:
            mailer.send(email)
        except MailError as exc:
            if exc.permanent:
                log.warning("Not retrying email to %s: %s", email.to, exc)
                return
            raise


def users_with_permission(permission: str) -> list[User]:
    """Verified, active people who hold ``permission`` through an explicit group."""
    groups = db.session.scalars(select(Group).where(Group.is_implicit.is_(False))).all()
    group_ids = [g.id for g in groups if g.slug == ADMINS or permission in (g.permissions or [])]
    if not group_ids:
        return []
    return list(
        db.session.scalars(
            select(User)
            .where(
                User.id.in_(select(GroupMembership.user_id).where(
                    GroupMembership.group_id.in_(group_ids), GroupMembership.status == "active",
                )),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                User.email_verified_at.is_not(None),
            )
            .order_by(User.id)
        ).all()
    )


# --------------------------------------------------------------------------- accounts


def verify_email(user: User) -> None:
    token = make_token("verify-email", {"uid": user.id, "email": user.email})
    send(
        "verify_email",
        user.email,
        f"Confirm your email address for {site_name()}",
        user=user,
        url=external_url("auth.verify_email", token=token),
    )


def change_email(user: User, new_email: str) -> None:
    token = make_token("change-email", {
        "uid": user.id, "email": new_email, "old_email": user.email, "uv": user.auth_version,
    })
    send(
        "change_email",
        new_email,
        f"Confirm your new email address for {site_name()}",
        user=user,
        new_email=new_email,
        url=external_url("account.confirm_email", token=token),
    )


def password_reset(user: User) -> None:
    send(
        "reset_password",
        user.email,
        f"Reset your {site_name()} password",
        user=user,
        url=external_url("auth.reset_password", token=reset_token(user)),
    )


def password_changed(user: User) -> None:
    send("password_changed", user.email, f"Your {site_name()} password was changed", user=user,
         url=external_url("auth.forgot_password"))


# --------------------------------------------------------------------------- subscribers


def subscriber_links(subscriber: Subscriber) -> dict[str, str]:
    return {
        "manage_url": external_url("subscribe.manage", token=subscriber.token),
        "unsubscribe_url": external_url("subscribe.unsubscribe", token=subscriber.token),
    }


def confirm_subscription(subscriber: Subscriber) -> None:
    token = make_token("confirm-subscription", {"sid": subscriber.id, "email": subscriber.email})
    send(
        "confirm_subscription",
        subscriber.email,
        f"Confirm your subscription to {site_name()}",
        subscriber=subscriber,
        url=external_url("subscribe.confirm", token=token),
    )


def welcome(subscriber: Subscriber) -> None:
    if not settings.get("newsletter.welcome_enabled"):
        return
    subject = str(settings.get("newsletter.welcome_subject") or "Welcome").replace("{site}", site_name())
    links = subscriber_links(subscriber)
    email = compose(
        "welcome",
        subscriber.email,
        subject,
        category="newsletter",
        subscriber=subscriber,
        body=settings.get("newsletter.welcome_body"),
        **links,
    )
    email.headers.update(unsubscribe_headers(links["unsubscribe_url"]))
    queue(email)


def unsubscribe_headers(unsubscribe_url: str) -> dict[str, str]:
    """RFC 8058 one-click unsubscribe, which Gmail and Yahoo expect from bulk senders."""
    return {
        "List-Unsubscribe": f"<{unsubscribe_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


# --------------------------------------------------------------------------- editorial


def _authors_to_notify(article: Article, exclude: Any = None) -> list[User]:
    exclude_id = getattr(exclude, "id", None)
    return [
        u for u in article.authors
        if u.id != exclude_id and u.is_active and not u.is_deleted and u.pref("submission_updates")
    ]


def submission_received(article: Article) -> None:
    for author in _authors_to_notify(article):
        send(
            "submission_received",
            author.email,
            f"We got your piece: {article.display_title}",
            user=author,
            article=article,
            url=external_url("write.status", article_id=article.id),
        )


def new_submission(article: Article, resubmitted: bool = False) -> None:
    editors = [u for u in users_with_permission("review") if u.pref("new_submissions") and not article.has_author(u)]
    if (article.assigned_editor and article.assigned_editor not in editors
            and permissions.has(article.assigned_editor, "review") and not article.has_author(article.assigned_editor)):
        editors.append(article.assigned_editor)
    verb = "Resubmitted" if resubmitted else "New submission"
    for editor in editors:
        send(
            "new_submission",
            editor.email,
            f"{verb}: {article.display_title}",
            user=editor,
            article=article,
            resubmitted=resubmitted,
            url=external_url("dash.article", article_id=article.id),
        )


STATUS_SUBJECTS = {
    "in_review": "An editor is reading “{title}”",
    "changes_requested": "Your editor asked for changes to “{title}”",
    "accepted": "“{title}” was accepted",
    "published": "“{title}” is live",
    "declined": "An update on “{title}”",
}


def status_changed(article: Article, old: str, new: str, actor: Any, message: str = "") -> None:
    subject_format = STATUS_SUBJECTS.get(new)
    if not subject_format:
        return
    url = (
        external_url("public.article", slug=article.slug)
        if new == "published"
        else external_url("write.status", article_id=article.id)
    )
    for author in _authors_to_notify(article, exclude=actor):
        send(
            "status_changed",
            author.email,
            subject_format.format(title=article.display_title),
            user=author,
            article=article,
            old=old,
            new=new,
            actor=actor,
            message=message,
            url=url,
        )


def comment_added(article: Article, event: ArticleEvent) -> None:
    actor = event.user
    if event.internal:
        # Internal notes are for editors only; they reach the assigned editor.
        recipients = [article.assigned_editor] if article.assigned_editor else []
    elif article.has_author(actor):
        # A writer replied: tell their editor, or every reviewer if nobody is assigned.
        recipients = [article.assigned_editor] if article.assigned_editor else [
            u for u in users_with_permission("review") if u.pref("new_submissions")
        ]
    else:
        # An editor wrote to the writer.
        recipients = _authors_to_notify(article, exclude=actor)
    seen: set[int] = set()
    for user in recipients:
        if user is None or user.id in seen or user.id == getattr(actor, "id", None):
            continue
        if not user.is_active or user.is_deleted or not user.is_verified:
            continue
        if event.internal and not permissions.has(user, "review"):
            continue
        seen.add(user.id)
        is_author = article.has_author(user)
        send(
            "comment",
            user.email,
            f"New comment on “{article.display_title}”",
            user=user,
            article=article,
            event=event,
            url=external_url("write.status" if is_author else "dash.article", article_id=article.id),
        )


def assigned(article: Article, editor: User, actor: Any) -> None:
    if editor.id == getattr(actor, "id", None):
        return
    send(
        "assigned",
        editor.email,
        f"You've been assigned “{article.display_title}”",
        user=editor,
        article=article,
        actor=actor,
        url=external_url("dash.article", article_id=article.id),
    )


# --------------------------------------------------------------------------- groups


def membership_requested(membership: GroupMembership) -> None:
    for admin in users_with_permission("manage_users"):
        if not admin.pref("group_requests"):
            continue
        send(
            "group_request",
            admin.email,
            f"{membership.user.display_name} asked to join {membership.group.name}",
            user=admin,
            membership=membership,
            url=external_url("dash.requests"),
        )


def membership_decided(membership: GroupMembership, approved: bool, reason: str = "") -> None:
    user = membership.user
    verb = "approved" if approved else "not approved"
    send(
        "group_decision",
        user.email,
        f"Your request to join {membership.group.name} was {verb}",
        user=user,
        group=membership.group,
        approved=approved,
        reason=reason,
        url=external_url("account.groups"),
    )
