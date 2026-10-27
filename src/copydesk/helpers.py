"""Flask-side helpers used by views, templates and background jobs."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from flask import abort, current_app, redirect, request, url_for
from sqlalchemy import Select, func, select

from copydesk.db import db
from copydesk.utils import Pagination, is_safe_redirect, parse_int


def state():
    """The per-app QuillSail state: config, hooks, themes and extensions."""
    return current_app.extensions["copydesk"]


def external_url(endpoint: str, **values: Any) -> str:
    """An absolute URL built from ``site.base_url``, for emails, feeds and the API."""
    return state().config.origin + url_for(endpoint, _external=False, **values)


def absolute(path: str) -> str:
    """Make a site-relative path (such as an upload's URL) absolute."""
    if path.startswith(("http://", "https://")):
        return path
    return state().config.origin + path


def json_object() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="Send a JSON object.")
    return data


def get_or_404(model: Any, ident: Any) -> Any:
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj


def first_or_404(stmt: Select) -> Any:
    obj = db.session.scalars(stmt.limit(1)).first()
    if obj is None:
        abort(404)
    return obj


def page_number() -> int:
    return max(1, parse_int(request.args.get("page"), 1) or 1)


def paginate(stmt: Select, page: int | None = None, per_page: int = 20) -> Pagination:
    page = page or page_number()
    total = db.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = list(db.session.scalars(stmt.limit(per_page).offset((page - 1) * per_page)).unique().all())
    return Pagination(items=items, page=page, per_page=per_page, total=total)


def next_url(default: str) -> str:
    target = request.values.get("next", "")
    if not is_safe_redirect(target):
        return default
    parts = urlsplit(target)
    decoded_path = unquote(parts.path)
    if not is_safe_redirect(decoded_path) or any(segment in {".", ".."} for segment in decoded_path.split("/")):
        return default
    prefix = request.script_root.rstrip("/")
    if prefix and parts.path != prefix and not parts.path.startswith(prefix + "/"):
        return urlunsplit(("", "", prefix + parts.path, parts.query, parts.fragment))
    return target


def redirect_next(default_endpoint: str, **values: Any):
    return redirect(next_url(url_for(default_endpoint, **values)))


def form_errors_summary(errors: dict[str, str]) -> str:
    count = len(errors)
    return "Please fix the highlighted field." if count == 1 else f"Please fix the {count} highlighted fields."
