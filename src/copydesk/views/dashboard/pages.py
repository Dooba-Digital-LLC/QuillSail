"""Custom pages (About, Writing guidelines, Privacy…) and sections."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import func, select, update

from copydesk import audit, settings
from copydesk import permissions as perms
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import get_or_404
from copydesk.models import Article, Page, Section
from copydesk.utils import parse_int, slugify, utcnow
from copydesk.views.dashboard import bp
from copydesk.views.public import RESERVED_PAGE_SLUGS


@bp.get("/pages/")
@perms.require("manage_pages")
def pages():
    rows = db.session.scalars(select(Page).order_by(Page.show_in_nav.desc(), Page.nav_position, Page.title)).all()
    special = {
        settings.get("submissions.guidelines_page"): "Linked from the writing editor",
        settings.get("accounts.terms_page"): "People agree to it when they sign up",
        "privacy": "Linked from every page footer and the sign-up form",
    }
    return render_template("dashboard/pages/list.html", pages=rows, special=special, dash_active="pages")


def _save_page(page: Page, form) -> dict[str, str]:
    errors: dict[str, str] = {}
    title = (form.get("title") or "").strip()
    slug = slugify(form.get("slug") or title, 80, "")
    if not title:
        errors["title"] = "Give the page a title."
    if not slug:
        errors["slug"] = "Give the page a web address."
    elif slug in RESERVED_PAGE_SLUGS:
        errors["slug"] = f"“{slug}” is used by QuillSail itself. Pick something else."
    elif db.session.scalar(select(Page.id).where(Page.slug == slug, Page.id != (page.id or 0))):
        errors["slug"] = "Another page already uses that address."
    if errors:
        return errors
    page.title = title[:200]
    page.slug = slug
    page.body = (form.get("body") or "").replace("\r\n", "\n")
    page.show_in_nav = bool(form.get("show_in_nav"))
    page.nav_position = parse_int(form.get("nav_position"), 0) or 0
    page.is_published = bool(form.get("is_published"))
    page.updated_at = utcnow()
    page.updated_by_id = current_user.id
    return errors


@bp.route("/pages/new", methods=["GET", "POST"])
@perms.require("manage_pages")
def page_new():
    page = Page(title="", slug="", body="", is_published=True)
    errors: dict[str, str] = {}
    if request.method == "POST":
        errors = _save_page(page, request.form)
        if not errors:
            db.session.add(page)
            db.session.flush()
            audit.record("page.created", page, actor=current_user)
            db.session.commit()
            flash("Page created.", "success")
            return redirect(url_for("dash.page_edit", page_id=page.id))
    return render_template("dashboard/pages/edit.html", page=page, errors=errors, creating=True,
                           form=request.form, dash_active="pages"), (422 if errors else 200)


@bp.route("/pages/<int:page_id>/", methods=["GET", "POST"])
@perms.require("manage_pages")
def page_edit(page_id: int):
    page = get_or_404(Page, page_id)
    errors: dict[str, str] = {}
    if request.method == "POST":
        if request.form.get("action") == "delete":
            title = page.title
            audit.record("page.deleted", page, actor=current_user)
            db.session.delete(page)
            db.session.commit()
            flash(f"Deleted “{title}”.", "success")
            return redirect(url_for("dash.pages"))
        errors = _save_page(page, request.form)
        if not errors:
            audit.record("page.updated", page, actor=current_user)
            db.session.commit()
            flash("Page saved.", "success")
            return redirect(url_for("dash.page_edit", page_id=page.id))
        db.session.rollback()
    return render_template("dashboard/pages/edit.html", page=page, errors=errors, creating=False,
                           form=request.form, dash_active="pages"), (422 if errors else 200)


@bp.route("/sections/", methods=["GET", "POST"])
@perms.require("manage_pages")
def sections():
    errors: dict[str, str] = {}
    if request.method == "POST":
        action = request.form.get("action", "add")
        section = db.session.get(Section, parse_int(request.form.get("section_id"))) if action != "add" else Section()
        if section is None:
            flash("That section no longer exists.", "error")
            return redirect(url_for("dash.sections"))
        if action == "delete":
            db.session.execute(update(Article).where(Article.section_id == section.id).values(section_id=None))
            audit.record("section.deleted", section, actor=current_user)
            db.session.delete(section)
            db.session.commit()
            flash(f"Deleted the {section.name} section. Its articles now have no section.", "success")
            return redirect(url_for("dash.sections"))
        name = (request.form.get("name") or "").strip()
        slug = slugify(request.form.get("slug") or name, 60, "")
        if not name:
            errors["name"] = "Give the section a name."
        elif db.session.scalar(select(Section.id).where(Section.slug == slug, Section.id != (section.id or 0))):
            errors["name"] = f"There's already a section at “{slug}”."
        if not errors:
            section.name = name[:80]
            section.slug = slug
            section.description = (request.form.get("description") or "").strip()[:500]
            section.position = parse_int(request.form.get("position"), section.position or 0) or 0
            section.accepts_submissions = bool(request.form.get("accepts_submissions")) if action != "add" else True
            if action == "add":
                section.position = (db.session.scalar(select(func.max(Section.position))) or 0) + 1
                db.session.add(section)
                db.session.flush()
            audit.record("section.created" if action == "add" else "section.updated", section, actor=current_user)
            db.session.commit()
            flash(f"Saved the {section.name} section.", "success")
            return redirect(url_for("dash.sections"))
        flash(next(iter(errors.values())), "error")
    rows = db.session.scalars(select(Section).order_by(Section.position, Section.name)).all()
    counts = dict(db.session.execute(
        select(Article.section_id, func.count()).where(Article.status == "published").group_by(Article.section_id)
    ).all())
    return render_template("dashboard/pages/sections.html", sections=rows, counts=counts, errors=errors,
                           dash_active="sections")
