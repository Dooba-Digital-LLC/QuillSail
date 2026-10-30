"""Subset the bundled Amiri Bold heading font to Latin for the website.

The application ships the full font (with Arabic) as TTF. The website only sets
Latin headings, so a WOFF2 subset keeps the download small. Amiri has no
Reserved Font Name, so the subset keeps its name under the SIL Open Font License.

    pip install 'fonttools[woff]'
    python website/scripts/build_fonts.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/copydesk/static/fonts"
OUTPUT = ROOT / "website/quillsail_site/static/fonts"

# The same "latin" range that common web font services use.
LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,"
    "U+0329,U+2000-206F,U+20AC,U+2122,U+2190-2193,U+2212,U+2215,U+FEFF,U+FFFD"
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["kern", "liga", "clig", "calt", "case", "lnum", "pnum", "tnum", "onum"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.desubroutinize = True
    font = subset.load_font(str(SOURCE / "Amiri-Bold.ttf"), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=subset.parse_unicodes(LATIN))
    subsetter.subset(font)
    target = OUTPUT / "Amiri-Bold-latin.woff2"
    subset.save_font(font, str(target), options)
    shutil.copy2(SOURCE / "OFL.txt", OUTPUT / "OFL.txt")
    print(f"{target.relative_to(ROOT)}: {target.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
