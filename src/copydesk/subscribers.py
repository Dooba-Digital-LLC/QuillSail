"""Newsletter subscriptions: signing up, confirming and unsubscribing.

Sign-up uses double opt-in: the address must be confirmed by clicking a link
before it gets any issues. This keeps typos and fake sign-ups off the list,
and many email providers expect it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from copydesk import notify
from copydesk.db import db
from copydesk.helpers import state
from copydesk.models import Group, Subscriber, User
from copydesk.utils import normalize_email, random_token, utcnow


def signup_groups() -> list[Group]:
    """Audience groups people can pick when they subscribe."""
    return list(
        db.session.scalars(
            select(Group)
            .where(Group.is_audience.is_(True), Group.on_signup_form.is_(True))
            .order_by(Group.position, Group.name)
        ).all()
    )


def audience_groups() -> list[Group]:
    return list(
        db.session.scalars(
            select(Group).where(Group.is_audience.is_(True)).order_by(Group.position, Group.name)
        ).all()
    )


def find(email: str) -> Subscriber | None:
    return db.session.scalar(select(Subscriber).where(Subscriber.email == normalize_email(email)))


def new_token() -> str:
    while True:
        token = random_token(24)
        if db.session.scalar(select(Subscriber.id).where(Subscriber.token == token)) is None:
            return token


def subscribe(
    email: str,
    *,
    name: str = "",
    groups: list[Group] | None = None,
    source: str = "form",
    user: User | None = None,
    confirmed: bool = False,
    send_confirmation: bool = True,
    welcome: bool = True,
) -> tuple[Subscriber, str]:
    """Start (or restart) a subscription.

    Returns the subscriber and what happened: "pending" (a confirmation email
    is on its way), "active" (subscribed straight away, e.g. a signed-in member
    with a confirmed address), or "already" (they were already subscribed).

    ``send_confirmation=False`` leaves the subscription pending without an
    email. New accounts use this: confirming the account's email address
    confirms the subscription too.
    """
    email = normalize_email(email)
    subscriber = find(email)
    if subscriber is not None and subscriber.status == "active":
        # Public sign-up is not proof of ownership of an existing address.
        # Only a verified owner may update an active subscription here.
        if confirmed:
            if groups:
                subscriber.groups = list({g.id: g for g in [*subscriber.groups, *groups]}.values())
            if name and not subscriber.name:
                subscriber.name = name[:100]
        return subscriber, "already"
    if subscriber is None:
        subscriber = Subscriber(email=email, token=new_token(), source=source)
        db.session.add(subscriber)
    subscriber.name = (name or subscriber.name or "")[:100]
    if groups is not None:
        subscriber.groups = list(groups)
    if user is not None and subscriber.user_id is None:
        subscriber.user_id = user.id
    elif subscriber.user_id is None:
        owner = db.session.scalar(select(User).where(User.email == email))
        if owner is not None and owner.is_verified:
            subscriber.user_id = owner.id
    subscriber.unsubscribed_at = None
    db.session.flush()
    if confirmed:
        activate(subscriber, welcome=welcome)
        return subscriber, "active"
    subscriber.status = "pending"
    if send_confirmation:
        notify.confirm_subscription(subscriber)
    return subscriber, "pending"


def activate(subscriber: Subscriber, *, welcome: bool = True) -> None:
    was_active = subscriber.status == "active"
    subscriber.status = "active"
    subscriber.confirmed_at = subscriber.confirmed_at or utcnow()
    subscriber.unsubscribed_at = None
    subscriber.bounce_count = 0
    if not was_active:
        if welcome:
            notify.welcome(subscriber)
        state().hooks.fire("subscriber.confirmed", subscriber=subscriber)


def unsubscribe(subscriber: Subscriber) -> None:
    if subscriber.status == "unsubscribed":
        return
    subscriber.status = "unsubscribed"
    subscriber.unsubscribed_at = utcnow()
    state().hooks.fire("subscriber.unsubscribed", subscriber=subscriber)


def for_user(user: Any) -> Subscriber | None:
    if not getattr(user, "is_authenticated", False) or not user.is_verified:
        return None
    subscriber = db.session.scalar(select(Subscriber).where(Subscriber.user_id == user.id))
    if subscriber is None:
        subscriber = find(user.email)
    return subscriber


def link_account(user: User) -> None:
    """Connect a newly verified account to an existing subscription with the same address."""
    subscriber = find(user.email)
    if subscriber is not None and subscriber.user_id is None:
        subscriber.user_id = user.id
