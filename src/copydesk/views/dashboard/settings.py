"""Site settings, built from the declarations in copydesk.settings."""

from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from copydesk import audit, notify, settings
from copydesk import permissions as perms
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import external_url, state
from copydesk.security import too_many
from copydesk.views.dashboard import bp

TAB_INTROS = {
    "general": "The basics readers see everywhere: your publication's name, who publishes it and how to reach you.",
    "appearance": "How the public site looks. Changes show up straight away.",
    "accounts": "Who can create an account, and what we ask when they do.",
    "writing": "How submissions work for writers.",
    "newsletter": "Sign-up forms and the emails subscribers get.",
    "extensions": "Settings added by the extensions you've turned on in copydesk.toml.",
}


@bp.route("/settings/", methods=["GET", "POST"], endpoint="settings", defaults={"tab": "general"})
@bp.route("/settings/<tab>", methods=["GET", "POST"], endpoint="settings")
@perms.require("manage_settings")
def settings_page(tab: str):
    if tab == "email":
        return redirect(url_for("dash.email_settings"))
    if tab == "appearance":
        from copydesk.views.dashboard.appearance import appearance_page

        return appearance_page()
    tabs = dict(settings.TABS)
    if tab not in tabs:
        abort(404)
    definitions = settings.for_tab(tab)
    errors: dict[str, str] = {}
    if request.method == "POST":
        values = {}
        for definition in definitions:
            raw = request.form.get(definition.key, settings.get(definition.key) if definition.key == "site.mode" else "")
            value, error = settings.parse_form_value(definition, raw)
            if error:
                errors[definition.key] = error
            values[definition.key] = value
        if not errors:
            changed = [k for k, v in values.items() if settings.get(k) != v]
            settings.set_values(values)
            if changed:
                audit.record("settings.updated", None, actor=current_user, label=", ".join(changed)[:255],
                             details={"tab": tab, "keys": changed})
            db.session.commit()
            flash("Settings saved." if changed else "Nothing changed.", "success")
            return redirect(url_for("dash.settings", tab=tab))
    extensions_by_name = {}
    for definition in definitions:
        extensions_by_name.setdefault(definition.extra.get("extension", ""), []).append(definition)
    return render_template(
        "dashboard/settings/edit.html",
        tab=tab,
        tabs=settings.TABS,
        intro=TAB_INTROS.get(tab, ""),
        definitions=definitions,
        grouped=extensions_by_name,
        errors=errors,
        form=request.form if request.method == "POST" else None,
        extensions=state().extensions,
        embed_code=_embed_code(),
        dash_active="settings",
    ), (422 if errors else 200)


def _embed_code() -> str:
    """HTML another website can paste in to collect sign-ups."""
    action = external_url("subscribe.subscribe")
    return (
        f'<form action="{action}" method="post">\n'
        '  <label for="cd-email">Email address</label>\n'
        '  <input id="cd-email" type="email" name="email" required>\n'
        '  <input type="text" name="website" tabindex="-1" autocomplete="off" hidden>\n'
        f'  <button type="submit">Subscribe to {settings.get("site.name")}</button>\n'
        "</form>"
    )


@bp.post("/settings/test-email")
@perms.require("manage_settings")
def settings_test_email():
    if too_many("test-email", 10, 3600, key=str(current_user.id)):
        flash("You've sent several test emails already. Please wait a bit.", "warning")
    else:
        notify.send("test", current_user.email, f"Test email from {settings.get('site.name')}", user=current_user)
        db.session.commit()
        flash(f"A test email is on its way to {current_user.email}. If it doesn't arrive within a few minutes, "
              "check System status for errors.", "success")
    return redirect(request.referrer or url_for("dash.system"))
