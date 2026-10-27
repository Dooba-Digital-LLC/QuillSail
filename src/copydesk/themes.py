"""Themes change how the public site looks.

A theme is a folder containing ``theme.toml`` and, optionally, ``templates/``
and ``static/``::

    themes/
      campus/
        theme.toml        name, title, description, parent, stylesheets
        static/theme.css
        templates/        overrides any template by its path, e.g. article.html

Templates are looked up in the active theme first, then its parent theme,
then extensions, then QuillSail's built-in templates. A theme only has to
contain the files it changes. See docs/theming.md.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context
from jinja2 import BaseLoader, TemplateNotFound
from jinja2.loaders import split_template_path

log = logging.getLogger("copydesk.themes")

PACKAGE_DIR = Path(__file__).parent
CORE_TEMPLATES = PACKAGE_DIR / "templates"
BUILTIN_THEMES = PACKAGE_DIR / "themes"


@dataclass
class Theme:
    name: str
    title: str
    path: Path
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    parent: str | None = None
    stylesheets: list[str] = field(default_factory=list)
    builtin: bool = False

    @property
    def templates_dir(self) -> Path:
        return self.path / "templates"

    @property
    def static_dir(self) -> Path:
        return self.path / "static"


def _read_theme(path: Path, builtin: bool) -> Theme | None:
    manifest = path / "theme.toml"
    try:
        with manifest.open("rb") as fh:
            data: dict[str, Any] = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("Skipping theme in %s: %s", path, exc)
        return None
    return Theme(
        name=str(data.get("name", path.name)),
        title=str(data.get("title", path.name.title())),
        description=str(data.get("description", "")),
        version=str(data.get("version", "1.0.0")),
        author=str(data.get("author", "")),
        parent=data.get("parent") or None,
        stylesheets=[str(s) for s in data.get("stylesheets", [])],
        path=path,
        builtin=builtin,
    )


def discover(extra_dir: Path | None) -> dict[str, Theme]:
    themes: dict[str, Theme] = {}
    for base, builtin in ((BUILTIN_THEMES, True), (extra_dir, False)):
        if base is None or not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if (child / "theme.toml").is_file():
                theme = _read_theme(child, builtin)
                if theme:
                    themes[theme.name] = theme
    return themes


def chain(themes: dict[str, Theme], name: str) -> list[Theme]:
    """The theme and its parents, nearest first. Falls back to "default"."""
    result: list[Theme] = []
    current = themes.get(name) or themes.get("default")
    while current is not None and current not in result:
        result.append(current)
        current = themes.get(current.parent) if current.parent else None
    return result


def available_themes() -> list[Theme]:
    if not has_app_context():
        return list(discover(None).values())
    return sorted(current_app.extensions["copydesk"].themes.values(), key=lambda t: (t.name != "default", t.title))


class ThemeLoader(BaseLoader):
    """Finds templates in the active theme, its parents, extensions, then the core."""

    def __init__(self, state: Any, theme_name: str | None = None) -> None:
        self.state = state
        self.theme_name = theme_name

    def search_path(self) -> list[Path]:
        dirs = [t.templates_dir for t in chain(self.state.themes, self.theme_name or self.state.active_theme)]
        dirs.extend(self.state.extension_template_dirs)
        dirs.append(CORE_TEMPLATES)
        return [d for d in dirs if d.is_dir()]

    def get_source(self, environment, template):
        pieces = split_template_path(template)
        for directory in self.search_path():
            path = directory.joinpath(*pieces)
            if path.is_file():
                source = path.read_text(encoding="utf-8")
                mtime = path.stat().st_mtime
                return source, str(path), lambda p=path, m=mtime: p.is_file() and p.stat().st_mtime == m
        raise TemplateNotFound(template)

    def list_templates(self):
        found = set()
        for directory in self.search_path():
            for path in directory.rglob("*"):
                if path.is_file():
                    found.add(path.relative_to(directory).as_posix())
        return sorted(found)
