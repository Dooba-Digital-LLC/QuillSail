"""The documentation site, built from the repository's Markdown files.

`website/docs.toml` lists the pages in reading order. Pages are the same files
people read on GitHub (`docs/*.md`, plus the changelog and contributor guides),
so there is one copy of the documentation. Everything is rendered once at
startup; every link between pages is checked, and links to other repository
files point to GitHub.
"""

from __future__ import annotations

import html
import posixpath
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from markupsafe import Markup

from quillsail_site.markdown import Heading, Renderer

# The navigation file, relative to the repository root.
NAV_FILE = "website/docs.toml"
WORD = re.compile(r"[\w.-]+")


@dataclass
class Page:
    slug: str
    file: str  # repository-relative path
    title: str  # short title for navigation
    heading: str = ""
    summary: str = ""
    html: Markup = Markup("")
    headings: list[Heading] = field(default_factory=list)
    section: str = ""
    sections: list[tuple[str, str, str]] = field(default_factory=list)
    previous: Page | None = None
    next: Page | None = None

    @property
    def path(self) -> str:
        return "/" + self.slug if self.slug else "/"

    @property
    def toc(self) -> list[Heading]:
        return [heading for heading in self.headings if heading.level in (2, 3)]


@dataclass
class Section:
    title: str
    pages: list[Page]


@dataclass(frozen=True)
class SearchResult:
    page: Page
    anchor: str
    heading: str
    snippet: Markup
    score: float

    @property
    def path(self) -> str:
        return self.page.path + (f"#{self.anchor}" if self.anchor else "")


class DocsError(ValueError):
    pass


class Docs:
    def __init__(self, repo_root: Path, *, repository: str, branch: str = "main", prefix: str = "",
                 nav_file: Path | None = None):
        self.root = repo_root.resolve()
        nav_file = nav_file or self.root / NAV_FILE
        self.repository, self.branch, self.prefix = repository, branch, prefix
        self.renderer = Renderer()
        self.sections: list[Section] = []
        self.pages: dict[str, Page] = {}
        self.by_file: dict[str, Page] = {}
        self.broken_links: list[str] = []
        self._internal_links: list[tuple[Page, str, str]] = []
        nav = tomllib.loads(nav_file.read_text(encoding="utf-8"))
        self.excluded = set(nav.get("exclude", []))
        for entry in nav["sections"]:
            section = Section(entry["title"], [])
            for item in entry["pages"]:
                file = item["file"]
                slug = item.get("slug", self._slug_for(file))
                if slug in self.pages or file in self.by_file:
                    raise DocsError(f"{file} appears twice in {nav_file.name}.")
                page = Page(slug, file, item["title"], section=section.title)
                section.pages.append(page)
                self.pages[slug] = page
                self.by_file[file] = page
            self.sections.append(section)
        ordered = [page for section in self.sections for page in section.pages]
        for before, after in zip(ordered, ordered[1:], strict=False):
            before.next, after.previous = after, before
        for page in ordered:
            self._render(page)
        self._check_internal_links()

    # --- building -------------------------------------------------------------------------

    @staticmethod
    def _slug_for(file: str) -> str:
        path = posixpath.splitext(file)[0]
        if path.startswith("docs/"):
            path = path[len("docs/"):]
        return "" if path == "README" else path.lower()

    def _render(self, page: Page) -> None:
        source = self.root / page.file
        if not source.is_file():
            raise DocsError(f"{page.file} is listed in the docs navigation but does not exist.")
        rendered = self.renderer.render(source.read_text(encoding="utf-8"), lambda href: self._resolve(page, href))
        page.heading = rendered.title or page.title
        page.summary = rendered.summary
        page.html = Markup(rendered.html)
        page.headings = rendered.headings
        page.sections = rendered.sections

    def _resolve(self, page: Page, href: str) -> str:
        parts = urlsplit(href)
        if parts.scheme or href.startswith("//"):
            return href
        if not parts.path:
            if parts.fragment:
                self._internal_links.append((page, page.slug, parts.fragment))
            return href
        directory = posixpath.dirname(page.file)
        target = posixpath.normpath(posixpath.join(directory, parts.path))
        if target.startswith("../") or target == "..":
            self.broken_links.append(f"{page.file}: {href} points outside the repository")
            return href
        linked = self.by_file.get(target)
        if linked:
            self._internal_links.append((page, linked.slug, parts.fragment))
            return self.url(linked) + (f"#{parts.fragment}" if parts.fragment else "")
        path = self.root / target
        if not path.exists():
            self.broken_links.append(f"{page.file}: {href} does not exist")
        kind = "tree" if path.is_dir() else "blob"
        return f"https://github.com/{self.repository}/{kind}/{self.branch}/{target}" + (
            f"#{parts.fragment}" if parts.fragment else "")

    def _check_internal_links(self) -> None:
        for page, slug, fragment in self._internal_links:
            target = self.pages[slug]
            if fragment and fragment not in {heading.id for heading in target.headings} and not fragment.startswith("fn"):
                self.broken_links.append(f"{page.file}: #{fragment} is not a heading in {target.file}")

    # --- reading ----------------------------------------------------------------------------

    def url(self, page: Page) -> str:
        return self.prefix + page.path

    def get(self, slug: str) -> Page | None:
        return self.pages.get(slug.strip("/"))

    def edit_url(self, page: Page) -> str:
        return f"https://github.com/{self.repository}/edit/{self.branch}/{page.file}"

    def source_url(self, page: Page) -> str:
        return f"https://github.com/{self.repository}/blob/{self.branch}/{page.file}"

    def unlisted_files(self) -> list[str]:
        """Markdown files under docs/ that are neither in the navigation nor excluded."""
        found = {path.relative_to(self.root).as_posix() for path in (self.root / "docs").rglob("*.md")}
        return sorted(found - set(self.by_file) - self.excluded)

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        terms = [term for term in WORD.findall(query.lower()) if len(term) > 1][:8]
        if not terms:
            return []
        results = []
        for page in self.by_file.values():
            title = f"{page.title} {page.heading}".lower()
            for anchor, heading, text in page.sections:
                lowered_heading, lowered_text = heading.lower(), text.lower()
                score = 0.0
                for term in terms:
                    in_title, in_heading, count = term in title, term in lowered_heading, lowered_text.count(term)
                    if not (in_title or in_heading or count):
                        break
                    score += 8 * in_title + 5 * in_heading + min(count, 6)
                else:
                    if not anchor:
                        score += 2  # Prefer a page's introduction when it matches as well.
                    results.append(SearchResult(page, anchor, heading or page.heading,
                                                _snippet(text, terms), score))
        results.sort(key=lambda result: (-result.score, result.page.file))
        return results[:limit]


def _snippet(text: str, terms: list[str], width: int = 180) -> Markup:
    lowered = text.lower()
    positions = [position for term in terms if (position := lowered.find(term)) >= 0]
    start = max(0, min(positions, default=0) - 50)
    if start:
        start = text.find(" ", start) + 1 or start
    excerpt = text[start:start + width]
    if start + width < len(text):
        excerpt = excerpt.rsplit(" ", 1)[0] + "…"
    if start:
        excerpt = "…" + excerpt
    escaped = html.escape(excerpt)
    pattern = re.compile("|".join(re.escape(html.escape(term)) for term in sorted(terms, key=len, reverse=True)),
                         re.IGNORECASE)
    return Markup(pattern.sub(lambda match: f"<mark>{match.group(0)}</mark>", escaped))
