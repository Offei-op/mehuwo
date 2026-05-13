"""Sanitise LLM-supplied text for reportlab Paragraph rendering.

Two jobs:
  - Escape characters that reportlab's mini-XML parser treats as markup
    (& < >) so they survive as literal text.
  - Convert the LaTeX snippets the LLM emits even when told not to
    (\\frac, \\div, \\times, $...$, etc.) into reportlab markup or plain
    Unicode so the PDF reads correctly.

Without the conversion, the PDF shows raw "3\\frac{1}{5}" and "$\\div$".
With it, the same JSON renders as a proper mixed number with sub/super
script and the correct division glyph.

The fraction conversion uses <super> and <sub> tags (per the pdf skill,
Unicode ⁰¹²₀₁₂... are unsafe in built-in fonts). DejaVu Sans renders
those tags correctly.
"""

from __future__ import annotations

import re


# \frac{a}{b}, tolerant of whitespace and nested braces (one level).
_FRAC_RE = re.compile(
    r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
)
# Mixed numbers like 3\frac{1}{5} -- the digit before \frac is already
# handled because the regex only consumes \frac{...}{...}; the leading
# integer survives verbatim. We just add a thin space so "3¹⁄₅" reads
# cleanly rather than running together.
_INT_FRAC_RE = re.compile(
    r"(\d)(<super>)",
)

# Common LaTeX commands -> Unicode. Note: ordered longest-first to avoid
# \neq matching \ne, etc.
_LATEX_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\\approx", "≈"),
    (r"\\times", "×"),
    (r"\\cdot", "·"),
    (r"\\div", "÷"),
    (r"\\neq", "≠"),
    (r"\\leq", "≤"),
    (r"\\geq", "≥"),
    (r"\\pm", "±"),
    (r"\\mp", "∓"),
    (r"\\sqrt", "√"),
    (r"\\infty", "∞"),
    (r"\\pi", "π"),
    (r"\\theta", "θ"),
    (r"\\alpha", "α"),
    (r"\\beta", "β"),
    (r"\\sum", "∑"),
    (r"\\to", "→"),
    (r"\\rightarrow", "→"),
    (r"\\leftarrow", "←"),
    (r"\\ldots", "…"),
    (r"\\dots", "…"),
]


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _delatex(s: str) -> str:
    """LaTeX -> reportlab markup / Unicode."""
    # 1) Fractions first (must run before stripping $...$, otherwise
    #    $\frac{1}{2}$ would lose the braces incorrectly).
    s = _FRAC_RE.sub(r"<super>\1</super>/<sub>\2</sub>", s)
    # 2) Mixed-number spacing: "3<super>" -> "3 <super>"
    s = _INT_FRAC_RE.sub(r"\1 \2", s)
    # 3) Math operators / Greek letters / common symbols
    for pat, repl in _LATEX_REPLACEMENTS:
        s = re.sub(pat, repl, s)
    # 4) Strip TeX math-mode dollars: $...$  ->  ...
    #    Run after operator substitution so e.g. $\div$ -> $÷$ -> ÷.
    s = re.sub(r"\$([^$]+)\$", r"\1", s)
    # 5) Lone dollar signs that didn't pair (rare but possible). Leave
    #    them: they probably mean currency. Same for any remaining
    #    backslash-words we don't recognise -- leave so the user can
    #    spot what slipped through.
    return s


def safe_para(s: str | None) -> str:
    """Make `s` safe to pass into a reportlab Paragraph: escape XML
    specials, then convert LaTeX to reportlab markup. Empty -> empty."""
    if s is None:
        return ""
    return _delatex(_html_escape(str(s)))
