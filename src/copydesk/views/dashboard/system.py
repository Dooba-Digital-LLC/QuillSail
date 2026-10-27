"""System status: email, background jobs, extensions and versions."""

from __future__ import annotations

import platform
from datetime import timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from copydesk import __version__, jobs
from copydesk import permissions as perms
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.email_connections import ConnectionProblem, effective_mail_config
from copydesk.helpers import get_or_404, state
from copydesk.mail import check_connection
from copydesk.models import Job
from copydesk.security import too_many
from copydesk.utils import utcnow
from copydesk.views.dashboard import bp


@bp.get("/system/")
@perms.require("manage_settings")
def system():
    config = state().config
    last_seen = jobs.worker_last_seen()
    failed = db.session.scalars(
        select(Job).where(Job.status == "failed").order_by(Job.finished_at.desc()).limit(25)
    ).all()
    upcoming = db.session.scalars(
        select(Job).where(Job.status.in_(("queued", "running"))).order_by(Job.run_at).limit(25)
    ).all()
    try:
        active_mail = effective_mail_config()
        mail_problem = ""
    except ConnectionProblem as exc:
        from dataclasses import replace

        active_mail = replace(config.mail, backend="unavailable", password="")
        mail_problem = str(exc)
    return render_template(
        "dashboard/system.html",
        config=config,
        mail_config=active_mail,
        mail_problem=mail_problem,
        version=__version__,
        python=platform.python_version(),
        database=db.engine.dialect.name if db.engine is not None else "unknown",
        counts=jobs.queue_counts(),
        last_seen=last_seen,
        worker_ok=last_seen is not None and utcnow() - last_seen < timedelta(minutes=5),
        failed=failed,
        upcoming=upcoming,
        extensions=state().extensions,
        themes=state().themes,
        active_theme=state().active_theme,
        dash_active="system",
    )


@bp.post("/system/jobs/run")
@perms.require("manage_settings")
def system_run_jobs():
    if too_many("run-jobs", 6, 600, key=str(current_user.id)):
        flash("Please wait a few minutes before running jobs by hand again.", "warning")
        return redirect(url_for("dash.system"))
    count = jobs.run_pending(max_jobs=25, max_seconds=20)
    flash(f"Ran {count} job{'s' if count != 1 else ''}. For regular sending, run a worker (see the installation guide).", "success")
    return redirect(url_for("dash.system"))


@bp.post("/system/jobs/<int:job_id>")
@perms.require("manage_settings")
def system_job(job_id: int):
    job = get_or_404(Job, job_id)
    if job.status == "running":
        abort(409, description="This job is running. Let it finish before changing it.")
    if request.form.get("action") == "retry":
        job.status = "queued"
        job.attempts = 0
        job.run_at = utcnow()
        job.finished_at = None
        flash("The job will be tried again.", "success")
    else:
        db.session.delete(job)
        flash("Job deleted.", "success")
    db.session.commit()
    return redirect(url_for("dash.system"))


@bp.post("/system/mail-check")
@perms.require("manage_settings")
def system_mail_check():
    problem = check_connection()
    if problem:
        flash(problem, "error")
    else:
        flash("The email connection is working.", "success")
    return redirect(url_for("dash.system"))
