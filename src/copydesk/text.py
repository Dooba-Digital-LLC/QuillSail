"""Turning Markdown into safe HTML, for the web and for email.

Writers use Markdown with a few extras: tables, footnotes, task lists, ~~strike~~,
automatic links, syntax-highlighted code blocks, and callouts::

    ::: tip Optional title
    Callouts come in three kinds: note, tip and warning.
    :::

Raw HTML in Markdown is never rendered, and the output is sanitized as a second
line of defense. Extensions can add Markdown plugins (see ``hooks``).
"""

from __future__ import annotations

import difflib
import re
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import nh3
from flask import current_app, has_app_context
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markupsafe import Markup
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from copydesk.utils import slugify, truncate, word_count

CALLOUTS = ("note", "tip", "warning")

ALLOWED_TAGS = {
    "a", "abbr", "aside", "b", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img",
    "input", "ins", "kbd", "li", "mark", "ol", "p", "pre", "s", "section", "span",
    "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "id", "class"},
    "img": {"src", "alt", "title", "loading", "width", "height"},
    "abbr": {"title"},
    "aside": {"class"},
    "code": {"class"},
    "div": {"class"},
    "hr": {"class"},
    "input": {"checked", "disabled", "class"},
    "li": {"id", "class"},
    "ol": {"start", "class"},
    "p": {"class"},
    "pre": {"class"},
    "section": {"class"},
    "span": {"class"},
    "sup": {"id", "class"},
    "td": {"class"},
    "th": {"class"},
    "ul": {"class"},
    **{f"h{n}": {"id"} for n in range(1, 7)},
}
URL_SCHEMES = {"http", "https", "mailto"}

_ALIGN_RE = re.compile(r'style="text-align:(left|right|center)"')
_PARAGRAPH_RE = re.compile(r"<p>(.*?)</p>", re.S)
_WEB_FORMATTER = HtmlFormatter(nowrap=True)


def _highlight(code: str, lang: str, _attrs: Any) -> str:
    lang = (lang or "").strip().lower()
    if not lang:
        return ""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return ""
    body = pygments_highlight(code, lexer, _WEB_FORMATTER)
    return f'<pre class="highlight"><code class="language-{escape(lang)}">{body}</code></pre>'


def _callout_renderer(name: str):
    def render(self, tokens, idx, _options, _env):
        token = tokens[idx]
        if token.nesting == 1:
            title = token.info.strip()[len(name):].strip() or name.capitalize()
            return (
                f'<aside class="callout callout-{name}">\n'
                f'<p class="callout-title">{escape(title)}</p>\n'
            )
        return "</aside>\n"

    return render


def _render_image(self, tokens, idx, options, env):
    tokens[idx].attrSet("loading", "lazy")
    return RendererHTML.image(self, tokens, idx, options, env)


def build_markdown(setups: list | tuple = ()) -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": True, "typographer": True, "highlight": _highlight},
    )
    md.enable(["table", "strikethrough", "linkify", "replacements", "smartquotes"])
    md.use(footnote_plugin)
    md.use(tasklists_plugin)
    md.use(anchors_plugin, min_level=2, max_level=4, slug_func=lambda s: slugify(s, 60, "section"))
    for name in CALLOUTS:
        md.use(container_plugin, name, render=_callout_renderer(name))
    md.add_render_rule("image", _render_image)
    for setup in setups:
        setup(md)
    return md


_default_markdown: MarkdownIt | None = None


def _markdown() -> MarkdownIt:
    global _default_markdown
    if has_app_context():
        state = current_app.extensions.get("copydesk")
        if state is not None:
            if state.markdown is None:
                state.markdown = build_markdown(state.hooks.markdown_setups)
            return state.markdown
    if _default_markdown is None:
        _default_markdown = build_markdown()
    return _default_markdown


def sanitize(html: str) -> str:
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        tag_attribute_values={"input": {"type": {"checkbox"}}},
        url_schemes=URL_SCHEMES,
        link_rel=None,
        strip_comments=True,
    )


def render_markdown(text: str | None, *, base_url: str = "") -> Markup:
    """Markdown to sanitized HTML for the website."""
    md = _markdown()
    env: dict = {}
    tokens = md.parse(text or "", env)
    if base_url:
        def resolve_links(tokens):
            for token in tokens:
                for attr in ("href", "src"):
                    value = token.attrGet(attr)
                    if value and not value.startswith("#"):
                        token.attrSet(attr, urljoin(base_url, value))
                if token.children:
                    resolve_links(token.children)
        resolve_links(tokens)
    html = md.renderer.render(tokens, md.options, env)
    html = _ALIGN_RE.sub(r'class="align-\1"', html)
    html = sanitize(html)
    if has_app_context() and "copydesk" in current_app.extensions:
        html = current_app.extensions["copydesk"].hooks.apply_filters("markdown.html", html)
    return Markup(html)


def render_inline(text: str | None) -> Markup:
    """Markdown for one line (no paragraphs), e.g. a short bio."""
    html = _markdown().renderInline(text or "")
    return Markup(sanitize(html))


def html_to_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(nh3.clean(str(html), tags=set()))).strip()


def excerpt(text: str | None, length: int = 220) -> str:
    """The first paragraph of some Markdown as plain text, cut to ``length``."""
    html = str(render_markdown(text))
    for match in _PARAGRAPH_RE.finditer(html):
        plain = html_to_text(match.group(1))
        if plain:
            return truncate(plain, length)
    return truncate(html_to_text(html), length)


def stats(text: str | None) -> dict[str, int]:
    words = word_count(text or "")
    return {"words": words, "minutes": max(1, round(words / 230)) if words else 0}


# --------------------------------------------------------------------------- email


class _EmailStyler(HTMLParser):
    """Rewrites sanitized HTML with inline styles, because many email apps ignore <style>."""

    VOID = {"br", "hr", "img", "input"}

    def __init__(self, styles: dict[str, str], token_styles: dict[str, str], origin: str) -> None:
        super().__init__(convert_charrefs=True)
        self.styles = styles
        self.token_styles = token_styles
        self.origin = origin.rstrip("/")
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.out.append(self._open(tag, dict(attrs)))
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.out.append(self._open(tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag in self.stack:
            while self.stack:
                if self.stack.pop() == tag:
                    break
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))

    def _open(self, tag: str, attrs: dict[str, str | None]) -> str:
        classes = (attrs.pop("class", None) or "").split()
        style = self.styles.get(tag, "")
        if tag == "code":
            style = self.styles["pre code"] if "pre" in self.stack else self.styles["code"]
        elif tag == "aside" and "callout" in classes:
            kind = next((c.split("-", 1)[1] for c in classes if c.startswith("callout-")), "note")
            style = self.styles.get(f"callout {kind}", self.styles["callout note"])
        elif tag == "p" and "callout-title" in classes:
            style = self.styles["callout title"]
        elif tag in ("th", "td"):
            for cls in classes:
                if cls.startswith("align-"):
                    style += f"text-align:{cls[6:]};"
        elif tag == "span":
            style = "".join(self.token_styles.get(cls, "") for cls in classes)
        for key in ("href", "src"):
            value = attrs.get(key)
            if value and value.startswith("/") and not value.startswith("//"):
                attrs[key] = self.origin + value
        if style:
            attrs["style"] = style
        parts = [tag]
        for key, value in attrs.items():
            parts.append(key if value is None else f'{key}="{escape(value)}"')
        return "<" + " ".join(parts) + ">"


def email_styles(accent: str) -> dict[str, str]:
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    mono = "SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
    styles = {
        "p": "margin:0 0 16px;font-size:16px;line-height:1.65;",
        "h1": f"margin:28px 0 12px;font-family:{font};font-size:26px;line-height:1.25;font-weight:700;",
        "h2": f"margin:28px 0 12px;font-family:{font};font-size:21px;line-height:1.3;font-weight:700;",
        "h3": f"margin:24px 0 10px;font-family:{font};font-size:18px;line-height:1.35;font-weight:700;",
        "h4": f"margin:20px 0 8px;font-family:{font};font-size:16px;line-height:1.4;font-weight:700;",
        "a": f"color:{accent};text-decoration:underline;",
        "blockquote": "margin:0 0 16px;padding:2px 0 2px 16px;border-left:3px solid #c9ced8;color:#4a505c;",
        "pre": "margin:0 0 16px;padding:12px 14px;background:#f4f5f7;border-radius:6px;"
               "white-space:pre-wrap;word-break:break-word;font-size:13px;line-height:1.5;",
        "pre code": f"font-family:{mono};font-size:13px;",
        "code": f"font-family:{mono};font-size:0.9em;background:#f1f2f4;padding:1px 4px;border-radius:4px;",
        "img": "max-width:100%;height:auto;border:0;display:block;margin:0 auto 16px;",
        "ul": "margin:0 0 16px;padding-left:24px;",
        "ol": "margin:0 0 16px;padding-left:24px;",
        "li": "margin:0 0 6px;font-size:16px;line-height:1.6;",
        "table": "border-collapse:collapse;width:100%;margin:0 0 16px;font-size:14px;",
        "th": "border:1px solid #d6dae1;padding:6px 8px;text-align:left;background:#f4f5f7;",
        "td": "border:1px solid #d6dae1;padding:6px 8px;",
        "hr": "border:0;border-top:1px solid #e3e6eb;margin:24px 0;",
        "callout note": "display:block;margin:0 0 16px;padding:12px 16px;background:#f2f5fb;border-left:4px solid #5b7bd5;",
        "callout tip": "display:block;margin:0 0 16px;padding:12px 16px;background:#eff8f2;border-left:4px solid #2f9e5c;",
        "callout warning": "display:block;margin:0 0 16px;padding:12px 16px;background:#fdf6ec;border-left:4px solid #d48a1c;",
        "callout title": "margin:0 0 6px;font-weight:700;font-size:14px;",
    }
    from flask import has_app_context

    if has_app_context():
        from copydesk.appearance import design, email_design

        d = design()
        if d["enabled"]:
            e = email_design(d)
            styles["p"] = e["p"]
            styles["li"] = e["p"].replace("0 0 16px", "0 0 6px")
            for tag, size in (("h1", 28), ("h2", 23), ("h3", 20), ("h4", 18)):
                styles[tag] = f"margin:24px 0 12px;font-family:{e['heading_font']};font-size:{size}px;line-height:1.3;color:{e['text']};"
            styles["blockquote"] = f"margin:0 0 16px;padding:2px 0 2px 16px;border-left:3px solid {e['border']};color:{e['muted']};"
            styles["a"] = f"color:{e['link']};text-decoration:underline;"
    return styles


_email_token_styles: dict[str, str] | None = None


def _token_styles() -> dict[str, str]:
    global _email_token_styles
    if _email_token_styles is None:
        formatter = HtmlFormatter(style="default")
        _email_token_styles = {
            cls: value[0].strip().rstrip(";") + ";" for cls, value in formatter.class2style.items() if value[0]
        }
    return _email_token_styles


def email_html(html: str | Markup, accent: str, origin: str) -> Markup:
    """Inline styles and absolute URLs so sanitized HTML survives email clients."""
    styler = _EmailStyler(email_styles(accent), _token_styles(), origin)
    styler.feed(str(html))
    styler.close()
    return Markup("".join(styler.out))


# --------------------------------------------------------------------------- diffs

_WORD_RE = re.compile(r"\s+|\w+|[^\w\s]")


def _word_diff(old: str, new: str) -> tuple[str, str]:
    a, b = _WORD_RE.findall(old), _WORD_RE.findall(new)
    left: list[str] = []
    right: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        old_part, new_part = escape("".join(a[i1:i2])), escape("".join(b[j1:j2]))
        if tag == "equal":
            left.append(old_part)
            right.append(new_part)
        else:
            if old_part:
                left.append(f"<del>{old_part}</del>")
            if new_part:
                right.append(f"<ins>{new_part}</ins>")
    return "".join(left), "".join(right)


def diff(old: str, new: str, context: int = 2) -> list[dict[str, Any]]:
    """A readable line-by-line diff, with changed words highlighted inside each line.

    Returns rows of ``{"kind": "same" | "add" | "del" | "skip", "html": ..., "count": ...}``.
    """
    a, b = (old or "").splitlines(), (new or "").splitlines()
    rows: list[dict[str, Any]] = []
    opcodes = difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            lines = a[i1:i2]
            head = lines[:context] if index > 0 else []
            tail = lines[-context:] if index < len(opcodes) - 1 else []
            if len(lines) <= len(head) + len(tail) + 1:
                rows.extend({"kind": "same", "html": escape(line)} for line in lines)
                continue
            rows.extend({"kind": "same", "html": escape(line)} for line in head)
            rows.append({"kind": "skip", "count": len(lines) - len(head) - len(tail)})
            rows.extend({"kind": "same", "html": escape(line)} for line in tail)
        elif tag == "replace":
            old_lines, new_lines = a[i1:i2], b[j1:j2]
            pairs = min(len(old_lines), len(new_lines))
            dels, adds = [], []
            for k in range(pairs):
                left, right = _word_diff(old_lines[k], new_lines[k])
                dels.append({"kind": "del", "html": left})
                adds.append({"kind": "add", "html": right})
            dels.extend({"kind": "del", "html": escape(line)} for line in old_lines[pairs:])
            adds.extend({"kind": "add", "html": escape(line)} for line in new_lines[pairs:])
            rows.extend(dels + adds)
        elif tag == "delete":
            rows.extend({"kind": "del", "html": escape(line)} for line in a[i1:i2])
        elif tag == "insert":
            rows.extend({"kind": "add", "html": escape(line)} for line in b[j1:j2])
    for row in rows:
        if "html" in row:
            row["html"] = Markup(row["html"] or "&nbsp;")
    return rows
