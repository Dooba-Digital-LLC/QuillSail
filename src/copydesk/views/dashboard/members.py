"""Members: accounts, their groups, approvals and blocking."""

from __future__ import annotations

import secrets

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select

from copydesk import audit, groups, notify
from copydesk import permissions as perms
from copydesk.auth import (
    current_user,
    hash_password,
    make_token,
    suggest_username,
    username_problem,
    verify_user,
)
from copydesk.db import db
from copydesk.helpers import external_url, get_or_404, paginate
from copydesk.models import ADMINS, Group, GroupMembership, User
from copydesk.utils import is_valid_email, normalize_email, parse_int
from copydesk.views.account import anonymize
from copydesk.views.dashboard import bp


def _all_groups() -> list[Group]:
    return list(db.session.scalars(
        select(Group).where(Group.is_implicit.is_(False)).order_by(Group.is_audience.desc(), Group.position)
    ).all())


@bp.get("/members/")
@perms.require("manage_users")
def members():
    q = (request.args.get("q") or "").strip().lower()
    group_id = parse_int(request.args.get("group"))
    state_filter = request.args.get("state", "active")
    stmt = select(User)
    if state_filter == "blocked":
        stmt = stmt.where(User.is_active.is_(False), User.deleted_at.is_(None))
    elif state_filter == "unverified":
        stmt = stmt.where(User.email_verified_at.is_(None), User.deleted_at.is_(None))
    elif state_filter == "deleted":
        stmt = stmt.where(User.deleted_at.is_not(None))
    else:
        state_filter = "active"
        stmt = stmt.where(User.is_active.is_(True), User.deleted_at.is_(None))
    if q:
        stmt = stmt.where(or_(
            func.lower(User.email).like(f"%{q}%"),
            func.lower(User.display_name).like(f"%{q}%"),
            func.lower(User.username).like(f"%{q}%"),
        ))
    if group_id:
        stmt = stmt.where(User.id.in_(
            select(GroupMembership.user_id).where(GroupMembership.group_id == group_id, GroupMembership.status == "active")
        ))
    pending = db.session.scalar(select(func.count()).where(GroupMembership.status == "pending")) or 0
    return render_template(
        "dashboard/members/list.html",
        page=paginate(stmt.order_by(User.created_at.desc()), per_page=50),
        q=q,
        group_id=group_id,
        state=state_filter,
        groups=_all_groups(),
        pending=pending,
        dash_active="members",
    )


@bp.route("/members/new", methods=["GET", "POST"])
@perms.require("manage_users")
def member_new():
    errors: dict[str, str] = {}
    form = request.form
    assignable = [g for g in _all_groups() if perms.can_assign(current_user, g)]
    if request.method == "POST":
        email = normalize_email(form.get("email"))
        name = (form.get("display_name") or "").strip()
        if not is_valid_email(email):
            errors["email"] = "Enter a valid email address."
        elif db.session.scalar(select(User.id).where(func.lower(User.email) == email)):
            errors["email"] = "There's already an account with that email address."
        if not name:
            errors["display_name"] = "Enter their name."
        if not errors:
            user = User(
                email=email,
                username=suggest_username(name, email),
                display_name=name[:100],
                password_hash=hash_password(secrets.token_urlsafe(32)),
            )
            db.session.add(user)
            db.session.flush()
            wanted = {parse_int(v) for v in form.getlist("groups")}
            for group in assignable:
                if group.id in wanted:
                    groups.add(user, group, actor=current_user)
            token = make_token("invite", {"uid": user.id, "ph": user.password_hash[-16:]})
            notify.send(
                "invite", email, f"You've been invited to {notify.site_name()}",
                user=user, inviter=current_user, url=external_url("auth.reset_password", token=token),
            )
            audit.record("user.registered", user, actor=current_user, details={"invited": True})
            db.session.commit()
            flash(f"Account created. We emailed {email} a link to choose a password.", "success")
            return redirect(url_for("dash.member", user_id=user.id))
    return render_template("dashboard/members/new.html", errors=errors, form=form, groups=assignable,
                           dash_active="members"), (422 if errors else 200)


@bp.route("/members/<int:user_id>/", methods=["GET", "POST"])
@perms.require("manage_users")
def member(user_id: int):
    user = get_or_404(User, user_id)
    if request.method == "POST":
        return _member_action(user)
    member_of = {m.group_id for m in user.memberships}
    can_manage_member = perms.can_manage_user(current_user, user)
    addable = [g for g in _all_groups()
               if can_manage_member and g.id not in member_of and perms.can_assign(current_user, g)]
    from copydesk.models import Article, ArticleAuthor
    from copydesk.subscribers import for_user

    articles = db.session.scalars(
        select(Article).join(ArticleAuthor, ArticleAuthor.article_id == Article.id)
        .where(ArticleAuthor.user_id == user.id, Article.status != "draft")
        .order_by(Article.updated_at.desc()).limit(10)
    ).all()
    return render_template(
        "dashboard/members/edit.html",
        member=user,
        addable=addable,
        articles=articles,
        subscriber=for_user(user),
        permissions=sorted(perms.permissions_for(user)),
        can_assign=lambda g: can_manage_member and perms.can_assign(current_user, g),
        can_manage_member=can_manage_member,
        is_self=user.id == current_user.id,
        viewer_is_admin=perms.is_admin(current_user),
        dash_active="members",
    )


def _member_action(user: User):
    if not perms.can_manage_user(current_user, user):
        abort(403)
    action = request.form.get("action", "")
    back = redirect(url_for("dash.member", user_id=user.id))
    if user.is_deleted and action != "noop":
        flash("This account was deleted.", "error")
        return back
    if action == "save":
        name = (request.form.get("display_name") or "").strip()
        username = (request.form.get("username") or "").strip().lower()
        problem = username_problem(username, exclude_user_id=user.id) if username != user.username else None
        if not name:
            flash("Enter a name.", "error")
        elif problem:
            flash(problem, "error")
        else:
            user.display_name, user.username = name[:100], username
            audit.record("user.updated", user, actor=current_user)
            db.session.commit()
            flash("Saved.", "success")
    elif action == "add_group":
        group = db.session.get(Group, parse_int(request.form.get("group_id")))
        if group is None or not perms.can_assign(current_user, group):
            flash("You can't add people to that group.", "error")
        else:
            groups.add(user, group, actor=current_user)
            db.session.commit()
            flash(f"Added to {group.name}.", "success")
    elif action in ("remove_membership", "approve", "reject"):
        membership = db.session.get(GroupMembership, parse_int(request.form.get("membership_id")))
        if membership is None or membership.user_id != user.id:
            abort(404)
        if not perms.can_assign(current_user, membership.group):
            flash("You can't change who's in that group.", "error")
        elif action == "approve":
            groups.approve(membership, current_user)
            db.session.commit()
            flash(f"Approved. {user.display_name} is now in {membership.group.name}.", "success")
        elif action == "reject":
            groups.reject(membership, current_user, request.form.get("reason", "").strip())
            db.session.commit()
            flash("Request turned down. They've been emailed.", "success")
        elif membership.group.slug == ADMINS and groups.last_admin(user):
            flash("They're the only administrator. Make someone else an administrator first.", "error")
        else:
            name = membership.group.name
            groups.remove(membership, current_user)
            db.session.commit()
            flash(f"Removed from {name}.", "success")
    elif action == "block":
        if user.id == current_user.id:
            flash("You can't block yourself.", "error")
        elif user.in_group(ADMINS) and not perms.is_admin(current_user):
            flash("Only administrators can block another administrator.", "error")
        elif user.in_group(ADMINS) and groups.last_admin(user):
            flash("They're the only administrator.", "error")
        else:
            user.is_active = False
            user.blocked_reason = (request.form.get("reason") or "").strip()[:255]
            user.auth_version += 1
            audit.record("user.blocked", user, actor=current_user, details={"reason": user.blocked_reason})
            db.session.commit()
            flash(f"{user.display_name} is blocked and has been signed out.", "success")
    elif action == "unblock":
        user.is_active = True
        user.blocked_reason = ""
        audit.record("user.unblocked", user, actor=current_user)
        db.session.commit()
        flash(f"{user.display_name} can sign in again.", "success")
    elif action == "verify":
        if not user.is_verified:
            verify_user(user)
            audit.record("user.verified", user, actor=current_user, details={"manual": True})
            db.session.commit()
            flash("Email address marked as confirmed.", "success")
    elif action == "reset":
        notify.password_reset(user)
        db.session.commit()
        flash(f"We emailed {user.email} a link to choose a new password.", "success")
    elif action == "delete":
        if user.id == current_user.id:
            flash("Delete your own account from your account settings.", "error")
        elif not perms.is_admin(current_user):
            flash("Only administrators can delete accounts.", "error")
        elif groups.last_admin(user):
            flash("They're the only administrator.", "error")
        else:
            anonymize(user, actor=current_user)
            db.session.commit()
            flash("The account was deleted. Published articles stay up, credited to “Former contributor”.", "success")
            return redirect(url_for("dash.members"))
    return back


@bp.get("/members/requests")
@perms.require("manage_users")
def requests():
    pending = [m for m in groups.pending_requests() if perms.can_assign(current_user, m.group)]
    return render_template("dashboard/members/requests.html", pending=pending, dash_active="members")


@bp.post("/members/requests/<int:membership_id>")
@perms.require("manage_users")
def request_decision(membership_id: int):
    membership = get_or_404(GroupMembership, membership_id)
    if membership.status != "pending":
        flash("That request was already handled.", "warning")
    elif not perms.can_assign(current_user, membership.group):
        abort(403)
    elif request.form.get("decision") == "approve":
        groups.approve(membership, current_user)
        db.session.commit()
        flash(f"{membership.user.display_name} is now in {membership.group.name}.", "success")
    else:
        name = membership.user.display_name
        groups.reject(membership, current_user, request.form.get("reason", "").strip())
        db.session.commit()
        flash(f"Turned down {name}'s request. They've been emailed.", "success")
    return redirect(url_for("dash.requests"))
