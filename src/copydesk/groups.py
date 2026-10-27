"""Joining and leaving groups, approvals, and the custom fields groups ask for.

Each group decides how people get in (``join_policy``):

* **open**: anyone can join straight away.
* **approval**: people ask, and someone who manages members says yes or no.
* **closed**: only administrators add people.

A group can also list email domains (``auto_join_domains``). Anyone who
confirms an address at one of them joins automatically, so students with a
university address skip the queue.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from copydesk import audit, notify
from copydesk import permissions as perms
from copydesk.db import db
from copydesk.helpers import state
from copydesk.models import ADMINS, Group, GroupMembership, User
from copydesk.utils import domain_matches, utcnow

FIELD_TYPES = (("text", "Short text"), ("number", "Number"), ("url", "Web address"), ("select", "Choice from a list"))


class GroupError(Exception):
    pass


def joinable_groups() -> list[Group]:
    """Audience groups people can choose for themselves (open or approval)."""
    return list(
        db.session.scalars(
            select(Group)
            .where(Group.is_audience.is_(True), Group.join_policy.in_(("open", "approval")), Group.is_implicit.is_(False))
            .order_by(Group.position, Group.name)
        ).all()
    )


def policy_text(group: Group) -> str:
    """Explain, in one line, how someone gets into ``group``."""
    domains = ", ".join("@" + d for d in group.auto_join_domains or [])
    if group.join_policy == "open":
        return "Anyone can join."
    if group.join_policy == "approval":
        if domains:
            return f"Automatic with an {domains} email address. Otherwise an editor approves your request."
        return "An editor will approve your request."
    return "Administrators add people to this group."


def parse_fields(group: Group, form: Any, prefix: str = "field") -> tuple[dict[str, Any], dict[str, str]]:
    """Read and check a group's custom fields from a submitted form."""
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for spec in group.fields or []:
        key = spec.get("key")
        if not key:
            continue
        name = f"{prefix}-{group.slug}-{key}"
        label = spec.get("label") or key
        raw = (form.get(name) or "").strip()[:200]
        kind = spec.get("type", "text")
        value: Any = raw
        if not raw:
            if spec.get("required"):
                errors[name] = f"Enter your {label[0].lower() + label[1:]}."
            data[key] = ""
            continue
        if kind == "number":
            try:
                value = int(raw)
            except ValueError:
                errors[name] = f"{label} must be a number."
        elif kind == "url" and not raw.startswith(("http://", "https://")):
            errors[name] = f"{label} must be a web address starting with https://"
        elif kind == "select" and raw not in (spec.get("options") or []):
            errors[name] = f"Choose one of the options for {label.lower()}."
        data[key] = value
    return data, errors


def add(user: User, group: Group, *, actor: Any, data: dict | None = None, source: str = "admin") -> GroupMembership:
    """Put someone in a group straight away (used by admins and domain rules)."""
    membership = user.membership(group)
    if membership is None:
        membership = GroupMembership(user=user, group=group, status="active", source=source, data=data or {})
        db.session.add(membership)
    else:
        membership.status = "active"
        if data is not None:
            membership.data = data
    membership.decided_at = utcnow()
    membership.decided_by_id = getattr(actor, "id", None)
    perms.forget(user)
    audit.record("membership.added", group, actor=actor, details={"user": user.username, "source": source})
    state().hooks.fire("membership.changed", membership=membership, action="added")
    return membership


def request(user: User, group: Group, *, data: dict | None = None, note: str = "") -> GroupMembership:
    """Ask to join ``group``. Returns the membership, active or pending."""
    if group.slug == ADMINS or group.is_implicit or group.join_policy == "closed":
        raise GroupError(f"You can't join {group.name} yourself. Ask an administrator.")
    membership = user.membership(group)
    if membership is not None:
        if data is not None:
            membership.data = data
        if note:
            membership.note = note[:1000]
        return membership
    automatic = user.is_verified and domain_matches(user.email, group.auto_join_domains or [])
    if group.join_policy == "open" or automatic:
        membership = add(user, group, actor=user, data=data, source="domain" if automatic else "self")
        return membership
    membership = GroupMembership(
        user=user, group=group, status="pending", source="self", data=data or {}, note=note[:1000]
    )
    db.session.add(membership)
    db.session.flush()
    audit.record("membership.requested", group, actor=user)
    state().hooks.fire("membership.requested", membership=membership)
    if user.is_verified:
        notify.membership_requested(membership)
    return membership


def after_verification(user: User) -> None:
    """Once someone confirms their email: apply domain rules and send pending requests on."""
    groups = db.session.scalars(select(Group).where(Group.is_implicit.is_(False))).all()
    for group in groups:
        if group.slug == ADMINS:
            continue
        if not group.auto_join_domains or not domain_matches(user.email, group.auto_join_domains):
            continue
        membership = user.membership(group)
        if membership is None and group.is_audience:
            add(user, group, actor=user, source="domain")
        elif membership is not None and membership.status == "pending":
            membership.source = "domain"
            add(user, group, actor=user, source="domain")
    for membership in user.memberships:
        if membership.status == "pending":
            notify.membership_requested(membership)


def approve(membership: GroupMembership, actor: User) -> None:
    membership.status = "active"
    membership.decided_at = utcnow()
    membership.decided_by_id = actor.id
    perms.forget(membership.user)
    audit.record("membership.approved", membership.group, actor=actor, details={"user": membership.user.username})
    state().hooks.fire("membership.changed", membership=membership, action="approved")
    notify.membership_decided(membership, approved=True)


def reject(membership: GroupMembership, actor: User, reason: str = "") -> None:
    group, user = membership.group, membership.user
    audit.record("membership.rejected", group, actor=actor, details={"user": user.username, "reason": reason})
    state().hooks.fire("membership.changed", membership=membership, action="rejected")
    notify.membership_decided(membership, approved=False, reason=reason)
    db.session.delete(membership)
    perms.forget(user)


def remove(membership: GroupMembership, actor: Any) -> None:
    group, user = membership.group, membership.user
    audit.record("membership.removed", group, actor=actor, details={"user": user.username})
    state().hooks.fire("membership.changed", membership=membership, action="removed")
    db.session.delete(membership)
    perms.forget(user)


def pending_requests() -> list[GroupMembership]:
    return list(
        db.session.scalars(
            select(GroupMembership)
            .where(GroupMembership.status == "pending")
            .order_by(GroupMembership.created_at)
        ).all()
    )


def last_admin(user: User) -> bool:
    """Is ``user`` the only active administrator? (They mustn't lose the role.)"""
    from copydesk.models import ADMINS

    admins = db.session.scalars(
        select(GroupMembership)
        .join(Group, Group.id == GroupMembership.group_id)
        .where(Group.slug == ADMINS, GroupMembership.status == "active")
    ).all()
    active = [m for m in admins if m.user.is_active and m.user.is_verified and not m.user.is_deleted]
    return len(active) == 1 and active[0].user_id == user.id
