"""Markdown for the docs: GitHub-compatible headings, links, callouts and code.

The docs are written to read well on GitHub too, so this renderer follows
GitHub's conventions: heading anchors use GitHub's slugs (existing links such
as `desktop.md#multiple-publications` keep working), and `> [!NOTE]` blocks
become callouts. Raw HTML is not rendered.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

CALLOUTS = {"NOTE": "Note", "TIP": "Tip", "IMPORTANT": "Important", "WARNING": "Warning", "CAUTION": "Caution"}
CALLOUT_MARK = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*\n?")
LANGUAGES = {
    "sh": ("bash", "Shell"), "bash": ("bash", "Shell"), "shell": ("bash", "Shell"), "console": ("bash", "Shell"),
    "zsh": ("bash", "Shell"), "powershell": ("powershell", "PowerShell"), "ps1": ("powershell", "PowerShell"),
    "pwsh": ("powershell", "PowerShell"), "toml": ("toml", "TOML"), "python": ("python", "Python"),
    "py": ("python", "Python"), "json": ("json", "JSON"), "css": ("css", "CSS"), "yaml": ("yaml", "YAML"),
    "yml": ("yaml", "YAML"), "ini": ("ini", "INI"), "dotenv": ("bash", "Environment"), "html": ("html", "HTML"),
    "jinja": ("html+jinja", "Jinja"), "nginx": ("nginx", "nginx"), "caddy": ("text", "Caddyfile"),
    "text": ("text", ""), "": ("text", ""),
}
FORMATTER = HtmlFormatter(nowrap=True)

# Resolves a link found in a page; returns the URL to render.
LinkResolver = Callable[[str], str]


@dataclass
class Heading:
    level: int
    id: str
    text: str


@dataclass
class Rendered:
    title: str
    html: str
    headings: list[Heading] = field(default_factory=list)
    summary: str = ""
    # (heading id or "", heading text, plain text) for search.
    sections: list[tuple[str, str, str]] = field(default_factory=list)


def github_slug(text: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation removed, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def plain_text(children) -> str:
    parts = []
    for child in children or []:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
        elif child.children:
            parts.append(plain_text(child.children))
    return "".join(parts)


class Renderer:
    def __init__(self):
        # Curly quotes only: the "replacements" rule would turn a bare --flag into an en dash.
        md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True})
        md.enable(["table", "strikethrough", "smartquotes"])
        md.use(footnote_plugin).use(tasklists_plugin).use(deflist_plugin)
        md.add_render_rule("fence", self._fence)
        md.add_render_rule("code_block", self._fence)
        md.add_render_rule("table_open", lambda *_: '<div class="table-wrap" tabindex="0"><table>\n')
        md.add_render_rule("table_close", lambda *_: "</table></div>\n")
        md.add_render_rule("blockquote_open", self._blockquote_open)
        md.add_render_rule("blockquote_close", self._blockquote_close)
        md.add_render_rule("heading_open", self._heading_open)
        md.add_render_rule("heading_close", self._heading_close)
        self.md = md

    def render(self, source: str, resolve: LinkResolver) -> Rendered:
        env: dict = {}
        tokens = self.md.parse(source, env)
        title = self._take_title(tokens)
        headings = self._assign_ids(tokens)
        self._mark_callouts(tokens)
        self._rewrite_links(tokens, resolve)
        html = self.md.renderer.render(tokens, self.md.options, env)
        return Rendered(title, html, headings, self._summary(tokens), self._sections(tokens))

    # --- passes over the token stream ---------------------------------------------------

    @staticmethod
    def _take_title(tokens) -> str:
        """Remove the page's first-level heading; the page template shows it."""
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and token.tag == "h1":
                title = plain_text(tokens[index + 1].children)
                del tokens[index:index + 3]
                return title
        return ""

    @staticmethod
    def _assign_ids(tokens) -> list[Heading]:
        seen: dict[str, int] = {}
        headings = []
        for index, token in enumerate(tokens):
            if token.type != "heading_open":
                continue
            text = plain_text(tokens[index + 1].children)
            base = github_slug(text) or "section"
            count = seen.get(base, 0)
            seen[base] = count + 1
            slug = base if count == 0 else f"{base}-{count}"
            token.attrSet("id", slug)
            token.meta["text"] = text
            headings.append(Heading(int(token.tag[1]), slug, text))
        return headings

    @staticmethod
    def _mark_callouts(tokens) -> None:
        for index, token in enumerate(tokens):
            if token.type != "blockquote_open" or index + 2 >= len(tokens):
                continue
            inline = tokens[index + 2]
            if tokens[index + 1].type != "paragraph_open" or inline.type != "inline":
                continue
            match = CALLOUT_MARK.match(inline.content)
            if not match:
                continue
            token.meta["callout"] = match.group(1)
            inline.content = inline.content[match.end():]
            children = inline.children or []
            if children and children[0].type == "text":
                children[0].content = CALLOUT_MARK.sub("", children[0].content, count=1)
                if not children[0].content:
                    children.pop(0)
                    if children and children[0].type == "softbreak":
                        children.pop(0)

    @staticmethod
    def _rewrite_links(tokens, resolve: LinkResolver) -> None:
        def walk(children):
            for child in children or []:
                if child.type == "link_open":
                    child.attrSet("href", resolve(child.attrGet("href") or ""))
                    href = child.attrGet("href") or ""
                    if href.startswith(("http://", "https://")):
                        child.attrSet("rel", "noopener")
                elif child.type == "image":
                    child.attrSet("src", resolve(child.attrGet("src") or ""))
                    child.attrSet("loading", "lazy")
                if child.children:
                    walk(child.children)

        for token in tokens:
            if token.type == "inline":
                walk(token.children)

    @staticmethod
    def _summary(tokens) -> str:
        for index, token in enumerate(tokens):
            if token.type == "paragraph_open" and token.level == 0:
                text = " ".join(plain_text(tokens[index + 1].children).split())
                return text if len(text) <= 180 else text[:177].rsplit(" ", 1)[0] + "…"
        return ""

    @staticmethod
    def _sections(tokens) -> list[tuple[str, str, str]]:
        sections = [["", "", []]]
        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                sections.append([token.attrGet("id"), token.meta.get("text", ""), []])
            elif token.type == "inline" and (index == 0 or tokens[index - 1].type != "heading_open"):
                sections[-1][2].append(plain_text(token.children))
            elif token.type in {"fence", "code_block"}:
                sections[-1][2].append(token.content)
        return [(anchor, heading, " ".join(" ".join(parts).split())) for anchor, heading, parts in sections
                if parts or heading]

    # --- render rules ---------------------------------------------------------------------

    @staticmethod
    def _fence(_renderer, tokens, index, options, env) -> str:
        token = tokens[index]
        name = (token.info or "").strip().split(maxsplit=1)[0].lower() if token.info else ""
        lexer_name, label = LANGUAGES.get(name, (name, name.upper()))
        try:
            lexer = get_lexer_by_name(lexer_name, stripnl=False)
            body = highlight(token.content, lexer, FORMATTER)
        except ClassNotFound:
            body = escapeHtml(token.content)
        caption = f'<figcaption class="code-label">{escapeHtml(label)}</figcaption>' if label else ""
        return (f'<figure class="code-block">{caption}'
                f'<pre class="highlight"><code>{body}</code></pre></figure>\n')

    @staticmethod
    def _blockquote_open(_renderer, tokens, index, options, env) -> str:
        kind = tokens[index].meta.get("callout")
        if not kind:
            return "<blockquote>\n"
        # Remember the kind for the closing tag.
        depth = 0
        for later in tokens[index + 1:]:
            if later.type == "blockquote_open":
                depth += 1
            elif later.type == "blockquote_close":
                if depth == 0:
                    later.meta["callout"] = kind
                    break
                depth -= 1
        return (f'<aside class="callout callout-{kind.lower()}" aria-label="{CALLOUTS[kind]}">'
                f'<p class="callout-label">{CALLOUTS[kind]}</p>\n')

    @staticmethod
    def _blockquote_close(_renderer, tokens, index, options, env) -> str:
        return "</aside>\n" if tokens[index].meta.get("callout") else "</blockquote>\n"

    @staticmethod
    def _heading_open(_renderer, tokens, index, options, env) -> str:
        token = tokens[index]
        return f'<{token.tag} id="{escapeHtml(token.attrGet("id") or "")}">'

    @staticmethod
    def _heading_close(_renderer, tokens, index, options, env) -> str:
        opening = tokens[index - 2]
        anchor = escapeHtml(opening.attrGet("id") or "")
        text = escapeHtml(opening.meta.get("text", ""))
        return (f'<a class="heading-anchor" href="#{anchor}" aria-label="Link to {text}">#</a>'
                f"</{tokens[index].tag}>\n")
