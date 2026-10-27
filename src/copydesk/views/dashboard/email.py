"""Administrator email setup with provider sign-in first, SMTP as an alternative."""

from functools import wraps

from flask import abort, flash, g, redirect, render_template, request, url_for

from copydesk import audit, notify, permissions, settings
from copydesk import email_connections as connections
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.mail import MailError, check_connection
from copydesk.security import default_csp, too_many
from copydesk.views.dashboard import bp


def administrator(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not permissions.is_admin(current_user):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _page(errors=None, values=None, status=200):
    g.csp_override = default_csp().replace(
        "form-action 'self'",
        "form-action 'self' https://accounts.google.com https://login.microsoftonline.com",
    )
    clients = {}
    for key, info in connections.PROVIDERS.items():
        try:
            config = connections.client_config(key)
        except connections.ConnectionProblem:
            config = {}
        clients[key] = {
            **info,
            "ready": bool(config.get("client_id") and config.get("client_secret")),
            "client_id": config.get("client_id", ""),
            "tenant": config.get("tenant", "common"),
            "callback": connections.callback_url(key),
        }
    current = connections.public_status()
    if values is None:
        values = {
            key: current.get(key, "")
            for key in ("host", "port", "security", "username", "rate_per_minute")
        }
        values["from_email"] = current.get("email", "")
    # Passwords and uploaded file contents are never reflected into HTML.
    values = {key: value for key, value in values.items() if key != "password"}
    return render_template(
        "dashboard/settings/email.html",
        tabs=settings.TABS,
        tab="email",
        clients=clients,
        connection=current,
        values=values,
        errors=errors or {},
        dash_active="settings",
    ), status


@bp.route("/settings/email", methods=["GET", "POST"])
@administrator
def email_settings():
    if request.method == "GET":
        return _page()
    action = request.form.get("action")
    if too_many("email-setup", 30, 3600, key=str(current_user.id)):
        abort(429)
    try:
        if action == "save_client":
            provider = request.form.get("provider")
            if provider not in connections.PROVIDERS:
                abort(400)
            file = request.files.get("credentials")
            if file and file.filename:
                content = file.read(connections.MAX_CONFIG_BYTES + 1)
                if len(content) > connections.MAX_CONFIG_BYTES:
                    raise connections.ConnectionProblem(
                        "Use an app credentials file smaller than 64 KB."
                    )
                connections.import_client(provider, content)
            else:
                connections.save_client(
                    provider,
                    request.form.get(provider + "_client_id", request.form.get("client_id")),
                    request.form.get(provider + "_client_secret", request.form.get("client_secret")),
                    request.form.get(provider + "_tenant", request.form.get("tenant", "common")),
                )
            audit.record(
                "email.app_configured", None, actor=current_user, details={"provider": provider}
            )
            db.session.commit()
            flash(
                f"{connections.PROVIDERS[provider]['name']} setup saved. You can now connect an account.",
                "success",
            )
        elif action in {"smtp", "import_smtp"}:
            if action == "import_smtp":
                file = request.files.get("config_file")
                if file is None or not file.filename:
                    raise connections.ConnectionProblem("Choose an SMTP .env or .toml file.")
                smtp = connections.parse_smtp_file(
                    file.read(connections.MAX_CONFIG_BYTES + 1), file.filename
                )
            else:
                _, current = connections.connection_data()
                smtp = connections.validate_smtp(request.form, current.get("smtp"))
            from copydesk.config import MailConfig

            problem = check_connection(MailConfig(**smtp))
            if problem:
                raise connections.ConnectionProblem(
                    "The SMTP server could not be reached or rejected sign-in. Check the host, TLS setting, username, and app password."
                )
            connections.save_connection({"provider": "smtp", "smtp": smtp})
            audit.record(
                "email.connected",
                None,
                actor=current_user,
                details={"provider": "smtp", "email": smtp["from_email"], "host": smtp["host"]},
            )
            db.session.commit()
            flash("SMTP connected. Send yourself a test email below.", "success")
        elif action == "disconnect":
            connections.save_connection({"provider": "server"})
            audit.record("email.disconnected", None, actor=current_user)
            db.session.commit()
            flash(
                "Email connection removed. The server’s configured email settings are now active.",
                "success",
            )
        elif action == "check":
            problem = check_connection()
            if problem:
                raise connections.ConnectionProblem(problem)
            flash("The email connection is working.", "success")
        elif action == "test":
            if too_many("email-connection-test", 5, 3600, key=str(current_user.id)):
                abort(429)
            notify.send(
                "test",
                current_user.email,
                f"Test email from {settings.get('site.name')}",
                user=current_user,
            )
            db.session.commit()
            flash(
                f"Test queued for {current_user.email}. The worker will send it using the current connection.",
                "success",
            )
        else:
            abort(400)
    except (connections.ConnectionProblem, MailError) as exc:
        db.session.rollback()
        settings.invalidate()
        return _page(
            {"connection": str(exc)}, dict(request.form) if action == "smtp" else None, 422
        )
    return redirect(url_for("dash.email_settings"))


@bp.post("/settings/email/connect/<provider>")
@administrator
def email_connect(provider):
    if provider not in connections.PROVIDERS:
        abort(404)
    if too_many("email-oauth", 10, 3600, key=str(current_user.id)):
        abort(429)
    try:
        authorize = connections.start_oauth(provider, current_user)
        db.session.commit()
    except connections.ConnectionProblem as exc:
        db.session.rollback()
        return _page({"connection": str(exc)}, status=422)
    return redirect(authorize)


@bp.get("/settings/email/callback/<provider>")
@administrator
def email_callback(provider):
    if provider not in connections.PROVIDERS:
        abort(404)
    try:
        email = connections.finish_oauth(
            provider,
            current_user,
            request.args.get("state", ""),
            "" if request.args.get("error") else request.args.get("code", ""),
        )
        audit.record(
            "email.connected",
            None,
            actor=current_user,
            details={"provider": provider, "email": email},
        )
        db.session.commit()
        flash(
            f"{connections.PROVIDERS[provider]['name']} connected: {email}. Send a test email to check delivery.",
            "success",
        )
        return redirect(url_for("dash.email_settings"))
    except connections.ConnectionProblem as exc:
        db.session.rollback()
        return _page({"connection": str(exc)}, status=400)
