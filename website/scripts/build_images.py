"""Render the website's raster images from the vector logo and the bundled fonts.

    pip install 'PySide6-Essentials>=6.8' Pillow   # or the repository's .[desktop] extra
    python website/scripts/build_images.py

Writes static/img/og.png (the 1200 × 630 link preview), apple-touch-icon.png
and favicon.ico. favicon.svg is the logo master itself. Run it on a Mac so the
preview uses the same Iowan Old Style and Avenir Next faces as the site; other
systems fall back to similar fonts.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGO = ROOT / "src/copydesk/static/brand/quillsail.svg"
AMIRI = ROOT / "src/copydesk/static/fonts/Amiri-Bold.ttf"
OUTPUT = ROOT / "website/quillsail_site/static/img"
PURPLE = QColor("#6e4cc2")
INK = QColor("#000000")


def first_family(*names: str) -> str:
    installed = set(QFontDatabase.families())
    return next((name for name in names if name in installed), names[-1])


def font(family: str, pixels: float, weight=QFont.Weight.Normal, italic=False, spacing=0.0) -> QFont:
    result = QFont(family)
    result.setPixelSize(round(pixels))
    result.setWeight(weight)
    result.setItalic(italic)
    if spacing:
        result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return result


def draw_logo(painter: QPainter, box: QRectF) -> None:
    renderer = QSvgRenderer(str(LOGO))
    renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
    renderer.render(painter, box)


def social_card(display: str, text: str, label: str) -> None:
    image = QImage(1200, 630, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    margin = 72.0

    # Folio line, as on the site's masthead.
    painter.setPen(INK)
    painter.setFont(font(label, 17, QFont.Weight.DemiBold, spacing=2.4))
    painter.drawText(QRectF(margin, 46, 1200 - 2 * margin, 24), Qt.AlignmentFlag.AlignLeft, "FREE, OPEN-SOURCE PUBLISHING SOFTWARE")
    painter.drawText(QRectF(margin, 46, 1200 - 2 * margin, 24), Qt.AlignmentFlag.AlignRight, "QUILLSAIL.BLOG")
    painter.fillRect(QRectF(margin, 84, 1200 - 2 * margin, 1.5), INK)

    # Nameplate: logo, then "Quill" in purple and "Sail" in ink, over a 2px rule.
    draw_logo(painter, QRectF(margin, 112, 88, 74))
    wordmark = font(display, 70, QFont.Weight.Bold)
    metrics = QFontMetricsF(wordmark)
    painter.setFont(wordmark)
    baseline = 174.0
    painter.setPen(PURPLE)
    painter.drawText(QRectF(margin + 108, baseline - metrics.ascent(), 600, metrics.height()), Qt.AlignmentFlag.AlignLeft, "Quill")
    painter.setPen(INK)
    painter.drawText(QRectF(margin + 108 + metrics.horizontalAdvance("Quill"), baseline - metrics.ascent(), 600, metrics.height()),
                     Qt.AlignmentFlag.AlignLeft, "Sail")
    painter.fillRect(QRectF(margin, 214, 1200 - 2 * margin, 3), INK)

    # The largest size at which the headline fits on one line.
    words, size = "Where readers write.", 160.0
    while QFontMetricsF(font(display, size, QFont.Weight.Bold)).horizontalAdvance(words) > 1200 - 2 * margin:
        size -= 2
    headline = font(display, size, QFont.Weight.Bold)
    painter.setFont(headline)
    head_metrics = QFontMetricsF(headline)
    painter.drawText(QRectF(margin - 3, 400 - head_metrics.ascent(), 1200, head_metrics.height()), Qt.AlignmentFlag.AlignLeft,
                     words)

    painter.setFont(font(text, 31, italic=False))
    painter.drawText(QRectF(margin, 478, 1200 - 2 * margin, 90), Qt.TextFlag.TextWordWrap,
                     "Self-hosted blogs and newsletters with reader submissions, editorial review and email delivery.")
    painter.setPen(QPen(INK, 1.5))
    painter.drawLine(int(margin), 586, int(1200 - margin), 586)
    painter.end()
    image.save(str(OUTPUT / "og.png"))


def icon(size: int, *, padding: float) -> QImage:
    """The logo on white, like the desktop app's icon tile but square for touch icons."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = size * padding
    draw_logo(painter, QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    painter.end()
    return image


def main() -> None:
    app = QGuiApplication([])
    family_id = QFontDatabase.addApplicationFont(str(AMIRI))
    if family_id < 0:
        raise RuntimeError("Amiri Bold could not be loaded.")
    display = QFontDatabase.applicationFontFamilies(family_id)[0]
    text = first_family("Iowan Old Style", "Charter", "Palatino", "Georgia")
    label = first_family("Avenir Next", "Helvetica Neue", "Helvetica", "Arial")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    social_card(display, text, label)
    icon(180, padding=0.16).save(str(OUTPUT / "apple-touch-icon.png"))
    transparent = []
    for size in (16, 32, 48):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_logo(painter, QRectF(0, size * 0.08, size, size * 0.84))
        painter.end()
        path = OUTPUT / f"favicon-{size}.png"
        image.save(str(path))
        transparent.append(path)
    with Image.open(transparent[-1]) as largest:
        largest.save(OUTPUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    for path in transparent:
        path.unlink()
    shutil.copy2(LOGO, OUTPUT / "favicon.svg")
    del app
    for name in ("og.png", "apple-touch-icon.png", "favicon.ico", "favicon.svg"):
        print(f"{name}: {(OUTPUT / name).stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
