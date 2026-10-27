"""Embed publication logos in outgoing MIME messages, without fetching URLs."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from io import BytesIO

from flask import current_app, has_app_context, url_for
from lxml import html as lxml_html
from PIL import Image, UnidentifiedImageError

from copydesk import settings
from copydesk.helpers import external_url, state

log = logging.getLogger("copydesk.mail")
MAX_LOGO_BYTES = 2 * 1024 * 1024


@dataclass
class InlineLogo:
    cid: str
    data: bytes


def inline_logos(html: str) -> tuple[str, list[InlineLogo]]:
    """Only configured, local publication logos are eligible for embedding.

    Keep HTTP URLs in browser previews and queued HTML. Resolve the file at
    MIME construction time so SMTP, Gmail and Outlook receive the same related
    image parts, including messages queued before this fix was installed.
    """
    if not has_app_context() or "copydesk" not in current_app.extensions:
        return html, []
    paths = {settings.get("appearance.email_logo"), settings.get("appearance.logo")} - {"", None}
    if not paths:
        return html, []
    if "<img" not in html.lower():
        return html, []
    sources = {}
    for path in paths:
        # Recognize canonical URLs, previous request-derived URLs, and relative
        # links from custom templates. No remote URL is ever downloaded.
        for src in (external_url("public.upload", path=path), url_for("public.upload", path=path, _external=True),
                    url_for("public.upload", path=path)):
            sources[src] = path
    document = lxml_html.document_fromstring(html)
    logos = {}
    attempted = set()
    root = state().config.uploads_dir.resolve()
    for image in document.iter("img"):
        path = sources.get(image.get("src", ""))
        if not path:
            continue
        if path not in attempted:
            attempted.add(path)
            try:
                target = (root / path).resolve()
                if not target.is_relative_to(root):
                    continue
                with target.open("rb") as file:
                    raw = file.read(MAX_LOGO_BYTES + 1)
                if len(raw) > MAX_LOGO_BYTES:
                    continue
                # PNG works across mail clients, including when the uploaded
                # website logo is WebP. Keep transparency and omit metadata.
                with Image.open(BytesIO(raw)) as original:
                    original.thumbnail((800, 240))
                    clean = original.convert("RGBA")
                    clean.info.clear()
                    buffer = BytesIO()
                    clean.save(buffer, "PNG", optimize=True)
                data = buffer.getvalue()
                cid = "quillsail-logo-" + hashlib.sha256(data).hexdigest()[:24] + "@inline"
                logos[path] = InlineLogo(cid, data)
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                log.warning("Publication logo could not be embedded; retaining its public image URL.")
        logo = logos.get(path)
        if logo:
            image.set("src", "cid:" + logo.cid)
    if not logos:
        return html, []
    unique = {logo.cid: logo for logo in logos.values()}
    return lxml_html.tostring(document, encoding="unicode", doctype="<!doctype html>"), list(unique.values())
