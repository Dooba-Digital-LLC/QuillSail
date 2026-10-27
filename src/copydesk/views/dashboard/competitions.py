"""Hosting, entry moderation and publication of competition results."""

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import delete, select

from copydesk import audit, settings
from copydesk import competitions as contest
from copydesk import permissions as perms
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import get_or_404, paginate
from copydesk.models import Competition, CompetitionEntry, CompetitionJudge, User
from copydesk.utils import local_to_utc, parse_datetime_local, parse_int, utcnow
from copydesk.views.dashboard import bp


def render(template, **kwargs):
    return render_template(
        "dashboard/competitions/" + template,
        competition_phase=contest.phase,
        competition_phases=contest.PHASES,
        competition_modes=contest.MODES,
        dash_active="competitions",
        **kwargs,
    )


@bp.get("/competitions/")
@perms.require("manage_competitions")
def competitions():
    page = paginate(
        select(Competition).order_by(Competition.created_at.desc(), Competition.id.desc()),
        per_page=20,
    )
    return render("index.html", page=page)


def save_form(item, form):
    errors = {}
    title = str(form.get("title") or "").strip()
    description = str(form.get("description") or "").strip()
    rules = str(form.get("rules") or "").strip()
    mode = form.get("mode")
    if not 1 <= len(title) <= 200:
        errors["title"] = "Use a title between 1 and 200 characters."
    if not 1 <= len(description) <= 30000:
        errors["description"] = "Describe the competition using up to 30,000 characters."
    if len(rules) > 30000:
        errors["rules"] = "Use up to 30,000 characters for the rules."
    if mode not in contest.MODES:
        errors["mode"] = "Choose judges, readers, or both."
    dates = {}
    for key in ("entries_close_at", "ends_at"):
        raw = form.get(key, "")
        parsed = parse_datetime_local(raw)
        if raw and not parsed:
            errors[key] = "Enter a valid date and time."
        dates[key] = local_to_utc(parsed, settings.get("site.timezone")) if parsed else None
    if (
        dates["entries_close_at"]
        and dates["ends_at"]
        and dates["ends_at"] <= dates["entries_close_at"]
    ):
        errors["ends_at"] = "Judging and voting must end after submissions close."
    judge_ids = []
    for raw in form.getlist("judge_ids"):
        value = parse_int(raw)
        if value is None:
            errors["judge_ids"] = "Choose judges from verified active members."
        else:
            judge_ids.append(value)
    judge_ids = sorted(set(judge_ids))
    eligible = db.session.scalars(
        select(User.id).where(
            User.id.in_(judge_ids),
            User.is_active.is_(True),
            User.email_verified_at.is_not(None),
            User.deleted_at.is_(None),
        )
    ).all()
    if set(eligible) != set(judge_ids):
        errors["judge_ids"] = "Choose judges from verified active members."
    if errors:
        return errors
    item.title, item.description, item.rules, item.mode = title, description, rules, mode
    for key, value in dates.items():
        setattr(item, key, value)
    if item.id is not None:
        db.session.execute(
            delete(CompetitionJudge).where(CompetitionJudge.competition_id == item.id)
        )
        for user_id in judge_ids if mode != "readers" else []:
            db.session.add(CompetitionJudge(competition_id=item.id, user_id=user_id))
    return {}


def edit_page(item, errors=None):
    members = db.session.scalars(
        select(User)
        .where(
            User.is_active.is_(True), User.email_verified_at.is_not(None), User.deleted_at.is_(None)
        )
        .order_by(User.display_name, User.id)
    ).all()
    selected = (
        request.form.getlist("judge_ids")
        if request.method == "POST"
        else [str(judge.user_id) for judge in item.judges]
    )
    return render(
        "edit.html",
        competition=item,
        errors=errors or {},
        values=request.form if request.method == "POST" else {},
        members=members,
        selected_judges=selected,
        timezone=settings.get("site.timezone"),
    ), 422 if errors else 200


@bp.route("/competitions/new", methods=["GET", "POST"])
@perms.require("manage_competitions")
def competition_new():
    item = Competition(
        title="",
        description="",
        rules="",
        mode="both",
        status="draft",
        created_by_id=current_user.id,
    )
    if request.method == "POST":
        # Validate before adding: an incomplete draft must never be flushed.
        errors = save_form(item, request.form)
        if errors:
            db.session.rollback()
            return edit_page(item, errors)
        db.session.add(item)
        db.session.flush()
        # save_form needs the new ID for its judge links.
        for user_id in (
            sorted({int(raw) for raw in request.form.getlist("judge_ids")})
            if item.mode != "readers"
            else []
        ):
            db.session.add(CompetitionJudge(competition_id=item.id, user_id=user_id))
        audit.record("competition.created", item, actor=current_user)
        db.session.commit()
        return redirect(url_for("dash.competition", competition_id=item.id))
    return edit_page(item)


@bp.route("/competitions/<int:competition_id>/edit", methods=["GET", "POST"])
@perms.require("manage_competitions")
def competition_edit(competition_id):
    item = get_or_404(Competition, competition_id)
    if request.method == "POST":
        item = contest.lock(item.id)
    if item.status != "draft":
        flash(
            "Competition setup is locked after opening. Create a new competition to use different rules.",
            "error",
        )
        db.session.rollback()
        return redirect(url_for("dash.competition", competition_id=item.id))
    if request.method == "POST":
        errors = save_form(item, request.form)
        if errors:
            db.session.rollback()
            return edit_page(item, errors)
        audit.record("competition.updated", item, actor=current_user)
        db.session.commit()
        flash("Competition saved.", "success")
        return redirect(url_for("dash.competition", competition_id=item.id))
    return edit_page(item)


@bp.get("/competitions/<int:competition_id>/")
@perms.require("manage_competitions")
def competition(competition_id):
    item = get_or_404(Competition, competition_id)
    entries = db.session.scalars(
        select(CompetitionEntry)
        .where(CompetitionEntry.competition_id == item.id)
        .order_by(CompetitionEntry.created_at, CompetitionEntry.id)
    ).all()
    return render(
        "detail.html",
        competition=item,
        phase=contest.phase(item),
        entries=entries,
        standings=contest.standings(item),
    )


@bp.post("/competitions/<int:competition_id>/phase")
@perms.require("manage_competitions")
def competition_phase(competition_id):
    item = contest.lock(competition_id)
    action = request.form.get("action")
    current = contest.phase(item)
    try:
        if action == "open" and current == "draft":
            if (item.entries_close_at and item.entries_close_at <= utcnow()) or (
                item.ends_at and item.ends_at <= utcnow()
            ):
                raise contest.CompetitionProblem(
                    "Choose future deadlines before opening the competition."
                )
            item.status = "submissions"
        elif action == "judge" and current == "submissions":
            item.status = "judging"
        elif action == "close" and current == "judging":
            item.status = "closed"
        elif action == "publish" and current == "closed":
            contest.publish_results(item)
        elif action == "delete" and current == "draft":
            audit.record("competition.deleted", item, actor=current_user)
            db.session.delete(item)
            db.session.commit()
            return redirect(url_for("dash.competitions"))
        else:
            raise contest.CompetitionProblem(
                "That phase change is no longer available. Reload the competition."
            )
        audit.record(
            "competition.phase_changed", item, actor=current_user, details={"phase": item.status}
        )
        db.session.commit()
        flash("Competition updated.", "success")
    except contest.CompetitionProblem as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("dash.competition", competition_id=competition_id))


@bp.post("/competitions/<int:competition_id>/entries/<int:entry_id>/moderate")
@perms.require("manage_competitions")
def competition_moderate(competition_id, entry_id):
    item = contest.lock(competition_id)
    entry = get_or_404(CompetitionEntry, entry_id)
    from flask import abort

    if entry.competition_id != item.id:
        abort(404)
    if item.status == "results" or entry.status == "withdrawn":
        flash("Published results and withdrawn entries cannot be changed.", "error")
    elif request.form.get("decision") not in {"approved", "rejected"}:
        abort(400)
    else:
        entry.status = request.form["decision"]
        entry.moderation_note = str(request.form.get("moderation_note_" + str(entry.id), request.form.get("moderation_note")) or "").strip()[:5000]
        entry.updated_at = utcnow()
        audit.record(
            "competition.entry_moderated",
            entry,
            actor=current_user,
            details={"status": entry.status},
        )
        db.session.commit()
        flash("Entry moderation saved.", "success")
    return redirect(url_for("dash.competition", competition_id=item.id))
