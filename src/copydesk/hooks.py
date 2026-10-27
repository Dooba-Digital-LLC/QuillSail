"""Extensions: add features without changing QuillSail itself.

An extension is a Python module (or package) that creates an ``Extension``
object named ``extension``::

    # extensions/shout/__init__.py
    from copydesk.hooks import Extension

    extension = Extension("shout", title="Shout", version="1.0.0")

    @extension.on("issue.sent")
    def announce(issue, **_):
        print(f"Issue #{issue.number} just went out!")

Turn it on in copydesk.toml::

    [extensions]
    enabled = ["shout"]

QuillSail looks for it in the extensions folder next to the config file, then
among the extensions bundled with QuillSail, then as an installed Python
package. docs/extensions.md lists every event, filter and template slot.

Three kinds of hook:

* **Events** (``@extension.on``): something happened. Handlers get keyword arguments.
* **Filters** (``@extension.filter``): change a value and return it.
* **Slots** (``@extension.slot``): return HTML to put at a named place in a template.

A failing handler is logged, not raised, so a broken extension can't stop an
issue from being sent. In tests, set ``hooks.raise_errors = True``.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markupsafe import Markup

log = logging.getLogger("copydesk.extensions")

EVENTS: dict[str, str] = {
    "app.ready": "app — the Flask app, once every extension has loaded",
    "user.registered": "user — someone created an account",
    "user.verified": "user — someone confirmed their email address",
    "user.deleted": "user — someone deleted their account (already anonymized)",
    "membership.requested": "membership — someone asked to join a group",
    "membership.changed": "membership, action — added, approved, rejected or removed",
    "subscriber.confirmed": "subscriber — a new subscriber confirmed their address",
    "subscriber.unsubscribed": "subscriber — someone unsubscribed",
    "article.created": "article — a new draft was started",
    "article.saved": "article, revision — an article's text was saved",
    "article.submitted": "article — a writer sent a piece to the editors",
    "article.status_changed": "article, old, new, actor, message",
    "article.published": "article — an article went live on the site",
    "article.commented": "article, event — someone added a comment",
    "issue.scheduled": "issue — an issue was scheduled",
    "issue.sending": "issue — an issue started sending",
    "issue.sent": "issue — every email for an issue has been sent",
}

FILTERS: dict[str, str] = {
    "markdown.html": "html — rendered article HTML, after sanitizing",
    "mail.before_send": "email — an Email about to be sent; return None to stop it",
    "issue.recipients": "subscribers, issue — the list of subscribers an issue will go to",
    "nav.items": "items, user — list of (label, url) links in the site header",
}

SLOTS: dict[str, str] = {
    "head": "Extra tags inside <head> on every page",
    "body_end": "Just before </body> on every page",
    "header_end": "End of the site header",
    "home_after_hero": "Home page, under the sign-up box",
    "article_before_body": "Article page, above the text",
    "article_after_body": "Article page, below the text",
    "issue_after": "Issue page, after the last item",
    "footer": "Inside the site footer",
    "dashboard_overview": "Dashboard home, under the summary cards",
    "account_profile": "Account settings, after the profile form",
    "email_footer": "Every email, above the footer",
}


class ExtensionError(Exception):
    pass


class Hooks:
    """The registry one app uses. Extensions add to it when they load."""

    def __init__(self) -> None:
        self.actions: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self.filters: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self.slots: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self.markdown_setups: list[Callable[..., Any]] = []
        self.raise_errors = False

    def on(self, event: str, fn: Callable[..., Any]) -> None:
        self.actions[event].append(fn)

    def add_filter(self, name: str, fn: Callable[..., Any]) -> None:
        self.filters[name].append(fn)

    def add_slot(self, name: str, fn: Callable[..., Any]) -> None:
        self.slots[name].append(fn)

    def fire(self, name: str, /, **kwargs: Any) -> None:
        for fn in list(self.actions.get(name, ())):
            try:
                fn(**kwargs)
            except Exception:
                log.exception("Extension handler %r for %r failed", fn, name)
                if self.raise_errors:
                    raise

    def apply_filters(self, name: str, value: Any, **kwargs: Any) -> Any:
        for fn in list(self.filters.get(name, ())):
            try:
                value = fn(value, **kwargs)
            except Exception:
                log.exception("Extension filter %r for %r failed", fn, name)
                if self.raise_errors:
                    raise
        return value

    def render_slot(self, name: str, **kwargs: Any) -> Markup:
        parts: list[str] = []
        for fn in list(self.slots.get(name, ())):
            try:
                html = fn(**kwargs)
            except Exception:
                log.exception("Extension slot %r for %r failed", fn, name)
                if self.raise_errors:
                    raise
                continue
            if html:
                parts.append(str(html))
        return Markup("\n".join(parts))


@dataclass
class DashboardLink:
    label: str
    endpoint: str
    permission: str | None = None


class Extension:
    """Describes an extension and collects what it adds."""

    def __init__(
        self,
        name: str,
        *,
        title: str | None = None,
        description: str = "",
        version: str = "0.1.0",
        author: str = "",
        url: str = "",
        template_folder: str | Path | None = None,
    ) -> None:
        self.name = name
        self.title = title or name.replace("_", " ").title()
        self.description = description
        self.version = version
        self.author = author
        self.url = url
        self.template_folder = Path(template_folder) if template_folder else None
        self.options: dict[str, Any] = {}
        self.blueprints: list[Any] = []
        self.commands: list[Any] = []
        self.dashboard_links: list[DashboardLink] = []
        self._actions: list[tuple[str, Callable[..., Any]]] = []
        self._filters: list[tuple[str, Callable[..., Any]]] = []
        self._slots: list[tuple[str, Callable[..., Any]]] = []
        self._markdown: list[Callable[..., Any]] = []
        self._setup: list[Callable[..., Any]] = []

    # --- decorators -------------------------------------------------------------------

    def on(self, event: str):
        def decorator(fn):
            self._actions.append((event, fn))
            return fn

        return decorator

    def filter(self, name: str):
        def decorator(fn):
            self._filters.append((name, fn))
            return fn

        return decorator

    def slot(self, name: str):
        def decorator(fn):
            self._slots.append((name, fn))
            return fn

        return decorator

    def markdown(self, fn):
        """Register ``fn(md)`` to customize the Markdown parser (add markdown-it plugins)."""
        self._markdown.append(fn)
        return fn

    def setup(self, fn):
        """Register ``fn(app)`` to run when the extension loads."""
        self._setup.append(fn)
        return fn

    def command(self, *args: Any, **kwargs: Any):
        """Add a command-line command: ``quillsail ext <name> ...``."""
        import click

        def decorator(fn):
            cmd = click.command(*args, **kwargs)(fn)
            self.commands.append(cmd)
            return cmd

        return decorator

    def job(self, kind: str):
        """Register a background job handler (see copydesk.jobs)."""
        from copydesk import jobs

        return jobs.handler(kind)

    # --- registrations ----------------------------------------------------------------

    def add_blueprint(self, blueprint: Any) -> None:
        self.blueprints.append(blueprint)

    def add_dashboard_link(self, label: str, endpoint: str, permission: str | None = None) -> None:
        self.dashboard_links.append(DashboardLink(label, endpoint, permission))

    def permission(self, key: str, label: str, description: str) -> None:
        from copydesk import permissions

        permissions.register(key, label, description, area=self.title)

    def setting(self, key: str, default: Any, label: str, **kwargs: Any) -> str:
        """Declare a setting editors can change under Settings → Extensions."""
        from copydesk import settings

        full_key = key if "." in key else f"{self.name}.{key}"
        kwargs.setdefault("tab", "extensions")
        kwargs.setdefault("extra", {})["extension"] = self.title
        settings.define(full_key, default, label, **kwargs)
        return full_key

    def option(self, key: str, default: Any = None) -> Any:
        """A value from this extension's table in copydesk.toml."""
        return self.options.get(key, default)

    # --- loading ----------------------------------------------------------------------

    def install(self, app: Any, state: Any) -> None:
        hooks: Hooks = state.hooks
        for event, fn in self._actions:
            hooks.on(event, fn)
        for name, fn in self._filters:
            hooks.add_filter(name, fn)
        for name, fn in self._slots:
            hooks.add_slot(name, fn)
        hooks.markdown_setups.extend(self._markdown)
        for blueprint in self.blueprints:
            app.register_blueprint(blueprint)
        if self.template_folder and self.template_folder.is_dir():
            state.extension_template_dirs.append(self.template_folder)
        state.dashboard_links.extend(self.dashboard_links)
        for fn in self._setup:
            fn(app)


def _load_module(name: str, extensions_dir: Path):
    candidates = [extensions_dir / name / "__init__.py", extensions_dir / f"{name}.py"]
    for path in candidates:
        if path.is_file():
            module_name = f"copydesk_local_extensions.{name}"
            if module_name in sys.modules:
                return sys.modules[module_name]
            spec = importlib.util.spec_from_file_location(
                module_name,
                path,
                submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
            )
            if spec is None or spec.loader is None:  # pragma: no cover - defensive
                raise ExtensionError(f"Could not load extension {name} from {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
            return module
    for dotted in (f"copydesk.extensions.{name}", name):
        try:
            return importlib.import_module(dotted)
        except ModuleNotFoundError as exc:
            # Keep looking only if the module itself is missing. A missing
            # dependency inside the extension is a real error.
            missing = exc.name or ""
            if not missing or not (dotted == missing or dotted.startswith(missing + ".")):
                raise
    raise ExtensionError(
        f"Extension {name!r} not found. Put it in {extensions_dir}/{name}/, or install it "
        f"as a Python package, then check the name in [extensions] enabled."
    )


def load_extensions(app: Any, state: Any, names: list[str], extensions_dir: Path, options: dict) -> None:
    for name in names:
        module = _load_module(name, extensions_dir)
        extension = getattr(module, "extension", None)
        if not isinstance(extension, Extension):
            raise ExtensionError(f"Extension module {name!r} must define `extension = Extension(...)`.")
        extension.options = dict(options.get(extension.name, options.get(name, {})))
        extension.install(app, state)
        state.extensions[extension.name] = extension
        log.info("Loaded extension %s %s", extension.name, extension.version)
