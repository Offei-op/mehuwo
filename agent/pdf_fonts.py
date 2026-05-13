"""Font registration helper. Registers a Unicode-capable TTF family so
glyphs like the Ghana cedi (\u20b5), em-dashes, and accented characters
render correctly. Falls back to Helvetica with a warning if no suitable
font is found.

Search order:
  1. $DEJAVU_FONT_DIR/DejaVuSans*.ttf   (env-var override)
  2. DejaVu Sans, common Linux paths
  3. DejaVu Sans, macOS Homebrew paths
  4. DejaVu Sans, common Windows install location
  5. Segoe UI (ships with Windows; has GH\u20b5)
  6. Arial (ships with Windows; has GH\u20b5 since Vista)
  7. Liberation Sans (Linux fallback)
  8. Helvetica with warning

Call register_unicode_font() once at module import. Idempotent.
"""

from __future__ import annotations

import os
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping


def _candidate(name: str, *, regular: str, bold: str, italic: str,
               bi: str) -> dict:
    return {"name": name, "regular": regular, "bold": bold,
            "italic": italic, "bi": bi}


def _env_override() -> list[dict]:
    """If DEJAVU_FONT_DIR is set, look there first. Useful for Windows
    users who pip-installed DejaVu or unzipped it locally."""
    d = os.environ.get("DEJAVU_FONT_DIR")
    if not d:
        return []
    return [_candidate(
        "DejaVuSans",
        regular=str(Path(d) / "DejaVuSans.ttf"),
        bold=str(Path(d) / "DejaVuSans-Bold.ttf"),
        italic=str(Path(d) / "DejaVuSans-Oblique.ttf"),
        bi=str(Path(d) / "DejaVuSans-BoldOblique.ttf"),
    )]


_FIXED_CANDIDATES = [
    # Linux: standard apt path for fonts-dejavu
    _candidate(
        "DejaVuSans",
        regular="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        bold="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        italic="/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        bi="/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    ),
    # macOS Homebrew
    _candidate(
        "DejaVuSans",
        regular="/opt/homebrew/share/fonts/DejaVuSans.ttf",
        bold="/opt/homebrew/share/fonts/DejaVuSans-Bold.ttf",
        italic="/opt/homebrew/share/fonts/DejaVuSans-Oblique.ttf",
        bi="/opt/homebrew/share/fonts/DejaVuSans-BoldOblique.ttf",
    ),
    # Windows: if user installed DejaVu via "Install for all users"
    _candidate(
        "DejaVuSans",
        regular=r"C:\Windows\Fonts\DejaVuSans.ttf",
        bold=r"C:\Windows\Fonts\DejaVuSans-Bold.ttf",
        italic=r"C:\Windows\Fonts\DejaVuSans-Oblique.ttf",
        bi=r"C:\Windows\Fonts\DejaVuSans-BoldOblique.ttf",
    ),
    # Windows default: Segoe UI ships with the OS and has GH\u20b5
    _candidate(
        "SegoeUI",
        regular=r"C:\Windows\Fonts\segoeui.ttf",
        bold=r"C:\Windows\Fonts\segoeuib.ttf",
        italic=r"C:\Windows\Fonts\segoeuii.ttf",
        bi=r"C:\Windows\Fonts\segoeuiz.ttf",
    ),
    # Windows fallback: Arial (cedi added in Vista and later)
    _candidate(
        "Arial",
        regular=r"C:\Windows\Fonts\arial.ttf",
        bold=r"C:\Windows\Fonts\arialbd.ttf",
        italic=r"C:\Windows\Fonts\ariali.ttf",
        bi=r"C:\Windows\Fonts\arialbi.ttf",
    ),
    # Linux fallback
    _candidate(
        "LiberationSans",
        regular="/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        bold="/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        italic="/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
        bi="/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
    ),
]


_registered: str | None = None


def register_unicode_font() -> str:
    """Register a Unicode-capable font family. Returns the family name
    to use for fontName=, or 'Helvetica' if nothing suitable was found
    (with a printed warning so the user knows why glyphs may be wrong)."""
    global _registered
    if _registered is not None:
        return _registered

    for cand in _env_override() + _FIXED_CANDIDATES:
        if not all(Path(cand[k]).exists()
                   for k in ("regular", "bold", "italic", "bi")):
            continue
        name = cand["name"]
        pdfmetrics.registerFont(TTFont(name, cand["regular"]))
        pdfmetrics.registerFont(TTFont(f"{name}-Bold", cand["bold"]))
        pdfmetrics.registerFont(TTFont(f"{name}-Italic", cand["italic"]))
        pdfmetrics.registerFont(TTFont(f"{name}-BoldItalic", cand["bi"]))
        # Map family so <b>, <i>, <b><i> tags resolve.
        addMapping(name, 0, 0, name)
        addMapping(name, 1, 0, f"{name}-Bold")
        addMapping(name, 0, 1, f"{name}-Italic")
        addMapping(name, 1, 1, f"{name}-BoldItalic")
        _registered = name
        return name

    print(
        "WARNING: no Unicode TTF found; falling back to Helvetica. "
        "Glyphs like GH\u20b5 will not render. Install fonts-dejavu, "
        "use a Windows machine with Segoe UI/Arial installed (default), "
        "or set DEJAVU_FONT_DIR to a directory containing DejaVuSans*.ttf.",
    )
    _registered = "Helvetica"
    return _registered
