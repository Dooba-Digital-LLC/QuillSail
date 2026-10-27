"""Groups and permissions: reader types, roles, join rules and custom fields."""

from __future__ import annotations

import re

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from copydesk import audit
from copydesk import groups as groups_api
from copydesk import permissions as perms
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import get_or_404
from copydesk.models import ADMINS, GROUP_COLORS, JOIN_POLICIES, Group, GroupMembership
from copydesk.utils import parse_int, parse_lines, slugify
from copydesk.views.dashboard import bp

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _member_counts() -> dict[int, dict[str, int]]:
    rows = db.session.execute(
        select(GroupMembership.group_id, GroupMembership.status, func.count())
        .group_by(GroupMembership.group_id, GroupMembership.status)
    ).all()
    counts: dict[int, dict[str, int]] = {}
    for group_id, status, count in rows:
        counts.setdefault(group_id, {})[status] = count
    return counts


@bp.get("/groups/", endpoint="groups")
@perms.require("manage_groups")
def group_list():
    everything = db.session.scalars(select(Group).order_by(Group.position, Group.name)).all()
    return render_template(
        "dashboard/groups/list.html",
        audience=[g for g in everything if g.is_audience],
        roles=[g for g in everything if not g.is_audience],
        counts=_member_counts(),
        policy_text=groups_api.policy_text,
        dash_active="groups",
    )


def _parse_fields(form) -> tuple[list[dict], list[str]]:
    """Read the custom-field rows (fields-<n>-label, fields-<n>-key, ...) from the form."""
    indices = sorted({int(m.group(1)) for key in form for m in [re.match(r"fields-(\d+)-label$", key)] if m})
    fields, problems, seen = [], [], set()
    for index in indices:
        label = (form.get(f"fields-{index}-label") or "").strip()[:80]
        if not label:
            continue
        key = (form.get(f"fields-{index}-key") or "").strip().lower() or slugify(label, 40, "field").replace("-", "_")
        if not FIELD_KEY_RE.match(key):
            problems.append(f"The field key “{key}” should start with a letter and use only a–z, 0–9 and _.")
            continue
        if key in seen:
            problems.append(f"Two fields use the key “{key}”. Each needs its own.")
            continue
        seen.add(key)
        kind = form.get(f"fields-{index}-type", "text")
        if kind not in ("text", "number", "url", "select"):
            kind = "text"
        spec = {
            "key": key,
            "label": label,
            "type": kind,
            "required": bool(form.get(f"fields-{index}-required")),
            "public": bool(form.get(f"fields-{index}-public")),
        }
        if kind == "select":
            spec["options"] = parse_lines(form.get(f"fields-{index}-options"))[:50]
            if not spec["options"]:
                problems.append(f"“{label}” is a choice field, so it needs some options.")
        fields.append(spec)
    return fields, problems


def _apply(group: Group, form, *, creating: bool) -> dict[str, str]:
    errors: dict[str, str] = {}
    name = (form.get("name") or "").strip()
    if not name:
        errors["name"] = "Give the group a name."
    elif len(name) > 60:
        errors["name"] = "Keep the name under 60 characters."
    slug = group.slug
    if creating or not group.is_system:
        slug = (form.get("slug") or "").strip().lower() or slugify(name, 40, "")
        if not SLUG_RE.match(slug or ""):
            errors["slug"] = "Use 2–40 lowercase letters, numbers and hyphens."
        elif db.session.scalar(select(Group.id).where(Group.slug == slug, Group.id != (group.id or 0))):
            errors["slug"] = "Another group already uses that short name."
    fields, problems = _parse_fields(form)
    if problems:
        errors["fields"] = " ".join(problems)
    template = (form.get("byline_template") or "").strip()[:160]
    if template:
        try:
            template.format_map({spec["key"]: "x" for spec in fields})
        except (KeyError, ValueError, IndexError):
            errors["byline_template"] = "Only use field keys from the list below, in {curly_brackets}."
    if errors:
        return errors

    group.slug = slug
    group.name = name
    group.member_label = (form.get("member_label") or "").strip()[:60]
    group.description = (form.get("description") or "").strip()[:500]
    group.color = form.get("color") if form.get("color") in GROUP_COLORS else "gray"
    group.show_badge = bool(form.get("show_badge"))
    group.position = parse_int(form.get("position"), group.position or 0) or 0
    if not group.is_system:
        group.is_audience = bool(form.get("is_audience"))
    if group.slug == ADMINS:
        group.join_policy = "closed"
        group.auto_join_domains = []
    elif not group.is_implicit:
        policy = form.get("join_policy", "closed")
        group.join_policy = policy if policy in JOIN_POLICIES else "closed"
        group.auto_join_domains = [d.lstrip("@").lower() for d in parse_lines(form.get("auto_join_domains"))][:20]
    group.on_signup_form = bool(form.get("on_signup_form")) and group.is_audience
    group.byline_template = template
    group.fields = fields

    if group.slug != ADMINS:
        mine = perms.permissions_for(current_user)
        wanted = set(form.getlist("permissions")) & perms.all_keys()
        current = set(group.permissions or [])
        # You can only grant or take away permissions you hold yourself.
        group.permissions = sorted((current - mine) | (wanted & mine))
    return errors


@bp.route("/groups/new", methods=["GET", "POST"], endpoint="group_new")
@perms.require("manage_groups")
def group_new():
    group = Group(is_audience=request.values.get("kind", "audience") == "audience", join_policy="approval",
                  color="blue", show_badge=True, on_signup_form=True, permissions=[], fields=[], position=50)
    errors: dict[str, str] = {}
    if request.method == "POST":
        errors = _apply(group, request.form, creating=True)
        if not errors:
            db.session.add(group)
            db.session.flush()
            audit.record("group.created", group, actor=current_user)
            db.session.commit()
            perms.forget()
            flash(f"Created the {group.name} group.", "success")
            return redirect(url_for("dash.group", group_id=group.id))
    return render_template("dashboard/groups/edit.html", group=group, errors=errors, creating=True,
                           **_form_context()), (422 if errors else 200)


@bp.route("/groups/<int:group_id>/", methods=["GET", "POST"], endpoint="group")
@perms.require("manage_groups")
def group_edit(group_id: int):
    group = get_or_404(Group, group_id)
    if not perms.can_manage_group(current_user, group):
        abort(403)
    errors: dict[str, str] = {}
    if request.method == "POST":
        errors = _apply(group, request.form, creating=False)
        if not errors:
            audit.record("group.updated", group, actor=current_user)
            db.session.commit()
            perms.forget()
            flash("Group saved.", "success")
            return redirect(url_for("dash.group", group_id=group.id))
    return render_template("dashboard/groups/edit.html", group=group, errors=errors, creating=False,
                           member_count=_member_counts().get(group.id, {}).get("active", 0),
                           **_form_context()), (422 if errors else 200)


def _form_context() -> dict:
    return {
        "form": request.form if request.method == "POST" else {},
        "areas": perms.by_area(),
        "mine": perms.permissions_for(current_user),
        "colors": GROUP_COLORS,
        "field_types": groups_api.FIELD_TYPES,
        "dash_active": "groups",
    }


@bp.post("/groups/<int:group_id>/delete", endpoint="group_delete")
@perms.require("manage_groups")
def group_delete(group_id: int):
    group = get_or_404(Group, group_id)
    if not perms.can_manage_group(current_user, group):
        abort(403)
    if group.is_system:
        abort(400)
    name = group.name
    audit.record("group.deleted", group, actor=current_user)
    db.session.delete(group)
    db.session.commit()
    perms.forget()
    flash(f"Deleted the {name} group. Its members are still members; they're just not in that group anymore.", "success")
    return redirect(url_for("dash.groups"))


@bp.get("/groups/permissions", endpoint="permission_matrix")
@perms.require_any("manage_groups", "manage_users")
def permission_matrix():
    everything = db.session.scalars(select(Group).order_by(Group.position, Group.name)).all()
    return render_template("dashboard/groups/matrix.html", groups=everything, areas=perms.by_area(),
                           dash_active="groups")
