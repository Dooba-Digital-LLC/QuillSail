"""Publication design tokens, portable presets and offline email styling.

Preview overrides live only in Flask's request-local settings cache. They never
change the app's template loader, settings rows, uploads or email queue.
"""

from __future__ import annotations

import json
import re
from html import escape

from flask import g, url_for
from markupsafe import Markup
from sqlalchemy import select

from copydesk import settings
from copydesk.colors import accent_tokens, ensure_contrast, mix

FONTS = {
    "serif": "Iowan Old Style,Charter,Palatino,Georgia,serif",
    "sans": "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif",
    "humanist": "Optima,Candara,Calibri,Trebuchet MS,sans-serif",
    "slab": "Rockwell,Roboto Slab,Georgia,serif",
    "mono": "SFMono-Regular,Consolas,Liberation Mono,monospace",
}
UPLOADS = {"logo": "Publication logo", "logo_dark": "Logo for dark backgrounds",
           "email_logo": "Email logo", "favicon": "Publication icon"}
PRESETS = {
    "classic": {"title": "Classic", "description": "A familiar publication with generous reading space.", "values": {}},
    "broadsheet": {"title": "Broadsheet", "description": "Ink, warm paper, serif headlines and strong editorial rules.", "values": {
        "accent_color": "#222222", "background": "#faf8f2", "surface": "#faf8f2", "text_color": "#20201e",
        "muted_color": "#62605a", "border_color": "#bdb9ae", "heading_font": "serif", "masthead": "center",
        "corner_radius": 0, "content_width": 1280, "reading_width": 760, "body_size": 19,
        "issue_layout": "columns", "email_layout": "editorial", "email_width": 680}},
    "brief": {"title": "Brief", "description": "A compact, fast-scanning digest with bold sans serif type.", "values": {
        "accent_color": "#3659c9", "article_font": "sans", "heading_font": "sans", "density": "compact",
        "body_size": 17, "line_height": 160, "reading_width": 700, "corner_radius": 4,
        "email_layout": "digest", "email_padding": 24}},
    "reference": {"title": "Reference", "description": "An orderly knowledge journal with restrained links and fine rules.", "values": {
        "accent_color": "#3366cc", "background": "#f8f9fa", "surface": "#ffffff", "text_color": "#202122",
        "muted_color": "#54595d", "border_color": "#a2a9b1", "article_font": "sans", "heading_font": "serif",
        "corner_radius": 0, "content_width": 1280, "reading_width": 880, "body_size": 17,
        "email_layout": "reference", "email_width": 680}},
    "canvas": {"title": "Canvas", "description": "Colorful cards, expressive headings and room for your own identity.", "values": {
        "accent_color": "#7440aa", "background": "#f4f0fa", "surface": "#ffffff", "text_color": "#292035",
        "muted_color": "#62546e", "border_color": "#ddd2e8", "article_font": "humanist", "heading_font": "slab",
        "corner_radius": 22, "density": "spacious", "issue_layout": "cards", "email_layout": "cards",
        "email_padding": 28}},
}

# Loading a preset resets visual controls, while retaining logos, custom code,
# homepage copy and installed template choice. Nothing is saved by loading it.
VISUAL_KEYS = [d.key for d in settings.for_tab("appearance") if d.key not in {
    "appearance.theme", "appearance.custom_css", "appearance.email_css", "appearance.home_intro",
    "appearance.show_subscriber_count", "appearance.brand_display", "appearance.logo_height",
    "appearance.custom_body_font", "appearance.custom_heading_font",
}]


def preset_values(name: str) -> dict:
    values = {key: settings.REGISTRY[key].default for key in VISUAL_KEYS}
    values.update({f"appearance.{key}": value for key, value in PRESETS[name]["values"].items()})
    values["appearance.style"] = name
    return values


def validate(values: dict) -> dict[str, str]:
    errors = {}
    for key in ("appearance.custom_body_font", "appearance.custom_heading_font"):
        value = values.get(key, "")
        if value and not re.fullmatch(r"[\w\s,'\".\-]+", value, flags=re.UNICODE):
            errors[key] = "Use font names separated by commas, without CSS declarations or URLs."
    for role in ("body", "heading"):
        key = "appearance.article_font" if role == "body" else "appearance.heading_font"
        if values.get(key) == "custom" and not values.get(f"appearance.custom_{role}_font", "").strip():
            errors[f"appearance.custom_{role}_font"] = "Enter a font stack, or choose one of the built-in typefaces."
    return errors


def export_design() -> str:
    return json.dumps({"format": "quillsail.appearance.v1", "settings": {
        d.key: settings.get(d.key) for d in settings.for_tab("appearance")
    }}, indent=2) + "\n"


def import_design(data: bytes) -> dict:
    if len(data) > 100_000:
        raise ValueError("Design files must be smaller than 100 KB.")
    try:
        document = json.loads(data)
        if document.get("format") != "quillsail.appearance.v1" or not isinstance(document.get("settings"), dict):
            raise ValueError
        allowed = {d.key: d for d in settings.for_tab("appearance")}
        values = {}
        for key, raw in document["settings"].items():
            if key not in allowed or not isinstance(raw, (str, int, bool)):
                raise ValueError
            value, error = settings.parse_form_value(allowed[key], str(raw) if not isinstance(raw, bool) else raw)
            if error:
                raise ValueError(error)
            values[key] = value
        combined = {key: settings.get(key) for key in allowed} | values
        if validate(combined):
            raise ValueError("Check the custom font stacks in this design.")
        return values
    except (UnicodeError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("Choose a QuillSail appearance JSON file.") from exc


def override(values: dict, images: dict | None = None) -> None:
    settings._cache().update(values)
    g.appearance_images = images or {}
    g.appearance_preview = True
    g.preview_theme = settings.get("appearance.theme")


def image_url(role: str = "logo", *, external: bool = False) -> str:
    from copydesk.helpers import external_url

    previews = g.get("appearance_images", {})
    if role in previews:
        return previews[role]
    path = settings.get(f"appearance.{role}")
    if not path:
        return ""
    return external_url("public.upload", path=path) if external else url_for("public.upload", path=path)


def email_brand() -> dict:
    """Explicit image dimensions keep wide logos within narrow email tables."""
    from copydesk.db import db
    from copydesk.models import Upload

    role = "email_logo" if image_url("email_logo") else "logo"
    src = image_url(role, external=True)
    sizes = g.setdefault("appearance_sizes", {})
    if role not in sizes:
        upload = db.session.scalar(select(Upload).where(Upload.path == settings.get(f"appearance.{role}"))) if src else None
        sizes[role] = (upload.width, upload.height) if upload else (240, 40)
    width, height = sizes[role]
    ratio = min(240 / max(1, width), 40 / max(1, height))
    return {"src": src, "width": max(1, round(width * ratio)), "height": max(1, round(height * ratio))}


def design() -> dict:
    d = {key.removeprefix("appearance."): settings.get(key) for key in settings.REGISTRY if key.startswith("appearance.")}
    d["enabled"] = settings.get("appearance.style", None) is not None or g.get("appearance_preview", False)
    d["body_font"] = FONTS.get(d["article_font"], d["custom_body_font"] or FONTS["sans"])
    d["heading_stack"] = d["body_font"] if d["heading_font"] == "inherit" else FONTS.get(
        d["heading_font"], d["custom_heading_font"] or FONTS["serif"])
    d["line_spacing"] = d["line_height"] / 100
    d["space"] = {"compact": 20, "comfortable": 32, "spacious": 44}.get(d["density"], 32)
    d["dark_background"] = d["dark_background"] or "#121419"
    d["dark_surface"] = d["dark_surface"] or "#181b21"
    d["dark_text"] = d["dark_text"] or "#e8eaee"
    d["dark_muted"] = mix(d["dark_background"], d["dark_text"], 0.7)
    d["dark_border"] = mix(d["dark_background"], d["dark_text"], 0.2)
    d["link"] = ensure_contrast(d["accent_color"], d["surface"], 4.5)
    d["dark_link"] = ensure_contrast(d["accent_color"], d["dark_surface"], 4.5)
    return d


def web_tokens(d: dict) -> Markup:
    if not d["enabled"]:
        return Markup("")
    # Values only come from typed settings; font strings cannot contain CSS/HTML.
    safe_font = lambda value: escape(value, quote=False).replace("<", "").replace(">", "")  # noqa: E731
    common = {
        "font-article": safe_font(d["body_font"]), "font-display": safe_font(d["heading_stack"]),
        "design-size": f"{d['body_size']}px", "design-leading": d["line_spacing"],
        "container": f"{d['content_width']}px", "measure": f"{d['reading_width']}px",
        "radius": f"{d['corner_radius']}px", "design-space": f"{d['space']}px", "logo-height": f"{d['logo_height']}px",
    }
    light = {"bg": d["background"], "surface": d["surface"], "surface-2": mix(d["surface"], d["text_color"], .04),
             "text": d["text_color"], "muted": d["muted_color"], "border": d["border_color"],
             "accent": d["link"], "accent-hover": d["link"], "accent-soft": mix(d["surface"], d["link"], .08),
             "on-accent": ensure_contrast("#ffffff", d["link"], 4.5)}
    dark = {"bg": d["dark_background"], "surface": d["dark_surface"], "surface-2": mix(d["dark_surface"], d["dark_text"], .05),
            "text": d["dark_text"], "muted": d["dark_muted"], "border": d["dark_border"],
            "accent": d["dark_link"], "accent-hover": d["dark_link"], "accent-soft": mix(d["dark_surface"], d["dark_link"], .12),
            "on-accent": ensure_contrast("#16181d", d["dark_link"], 4.5)}
    for palette in (light, dark):
        palette.update({"text-muted": palette["muted"], "text-subtle": palette["muted"],
                        "bg-subtle": palette["surface-2"], "bg-muted": palette["surface-2"],
                        "surface-raised": palette["surface"], "border-strong": palette["border"]})
    declarations = lambda values: ";".join(f"--{k}:{v}" for k, v in values.items())  # noqa: E731
    return Markup(".publication{" + declarations(common | light) + "}"
                  "@media(prefers-color-scheme:dark){:root:not([data-theme=light]) .publication{" + declarations(dark) + "}}"
                  ":root[data-theme=dark] .publication{" + declarations(dark) + "}")


def inline_email_css(html: str) -> str:
    css = settings.get("appearance.email_css")
    if not css.strip():
        return html
    import cssutils
    from lxml import html as lxml_html
    from premailer import Premailer

    # cssutils otherwise resolves @import while parsing, independently of
    # Premailer's network switch. Disable its fetcher and discard import rules.
    parser = cssutils.CSSParser(fetcher=lambda url: (None, b""), loglevel=50)
    sheet = parser.parseString(css)
    for rule in list(sheet.cssRules):
        if rule.type in (rule.IMPORT_RULE, rule.CHARSET_RULE):
            sheet.deleteRule(rule)
    safe_css = sheet.cssText.decode("utf-8").replace("<", "\\3c ")
    # Premailer always gives an existing style attribute precedence, even over
    # !important. Move the generated base styles into a stylesheet first, so
    # an administrator's overrides participate in the normal CSS cascade.
    document = lxml_html.document_fromstring(html)
    base_rules = []
    for index, element in enumerate(document.xpath("//*[@style]")):
        name = f"qs-inline-{index}"
        base_rules.append(f".{name}{{{element.attrib.pop('style')}}}")
        element.attrib["class"] = (element.attrib.get("class", "") + " " + name).strip()
    return Premailer(lxml_html.tostring(document, encoding="unicode"), css_text="\n".join(base_rules) + "\n" + safe_css,
                     allow_network=False, allow_loading_external_files=False, include_star_selectors=True,
                     disable_link_rewrites=True, disable_validation=True, keep_style_tags=False,
                     strip_important=False).transform()


def email_design(d: dict) -> dict:
    from copydesk.notify import EMAIL_STYLE

    e = dict(EMAIL_STYLE)
    enabled = d["enabled"]
    e.update(width=d["email_width"] if enabled else 600, padding=d["email_padding"] if enabled else 32,
             size=d["email_size"] if enabled else 16, leading=d["line_spacing"] if enabled else 1.65,
             background=d["background"] if enabled else "#f3f4f6", surface=d["surface"] if enabled else "#ffffff",
             text=d["text_color"] if enabled else "#16181d", muted=d["muted_color"] if enabled else "#6b7280",
             border=d["border_color"] if enabled else "#e5e7eb", radius=d["corner_radius"] if enabled else 12,
             font=d["body_font"] if enabled else EMAIL_STYLE["font"],
             heading_font=d["heading_stack"] if enabled else EMAIL_STYLE["font"],
             layout=d["email_layout"] if enabled else "classic", align=d["masthead"] if enabled else "left",
             link=d["link"] if enabled else accent_tokens(d["accent_color"])["accent"])
    e["soft"] = mix(e["surface"], e["text"], .04)
    for key, size in (("h1", 32 if e["layout"] == "editorial" else 28), ("h2", 23 if e["layout"] == "editorial" else 21)):
        e[key] = f"margin:0 0 12px;font-family:{e['heading_font']};font-size:{size}px;line-height:1.25;color:{e['text']};font-weight:700;"
    e["p"] = f"margin:0 0 16px;font-family:{e['font']};font-size:{e['size']}px;line-height:{e['leading']};color:{e['text']};"
    e["small"] = f"margin:0 0 12px;font-family:{e['font']};font-size:13px;line-height:1.5;color:{e['muted']};"
    e["label"] = f"margin:0 0 8px;font-family:{e['font']};font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{e['muted']};"
    e["story"] = f"margin:24px 0 0;padding:24px 0 0;border-top:1px solid {e['border']};"
    if e["layout"] == "digest":
        e["story"] = f"margin:18px 0 0;padding:0 0 0 14px;border-left:3px solid {e['link']};"
    elif e["layout"] == "cards":
        e["story"] = f"margin:20px 0 0;padding:20px;background:{e['soft']};border:1px solid {e['border']};border-radius:{e['radius']}px;"
    elif e["layout"] == "reference":
        e["story"] = f"margin:24px 0 0;padding:12px 0 0;border-top:1px solid {e['border']};"
    return e
