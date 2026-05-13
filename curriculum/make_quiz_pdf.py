"""
make_quiz_pdf.py
================

Generate an A4 question-sheet PDF and a matching answer-key PDF from the
fraction diagnostic item bank JSON (the file produced by the earlier
generation step).

Usage
-----
    pip install reportlab

    # default: include single + multi skill items, deterministic order
    python make_quiz_pdf.py item_bank.json

    # shuffle item order and option order with a seed
    python make_quiz_pdf.py item_bank.json --shuffle-items --shuffle-options --seed 42

    # only single-skill items
    python make_quiz_pdf.py item_bank.json --include single_skill_items

    # custom output paths
    python make_quiz_pdf.py item_bank.json --output forms/quiz_v1.pdf --key-output forms/key_v1.pdf

Output
------
Two PDFs:
  - <output>           the student-facing question sheet (no answers visible)
  - <output>_key.pdf   the marker's answer key with item id, correct letter,
                       target node(s), and the q_vector

Notes
-----
Vulgar-fraction unicode characters (½, ⅓, ¾, ⅖ …) are normalised to slash
notation (1/2, 1/3, 3/4, 2/5 …) before rendering because reportlab's
built-in Helvetica does not contain glyphs for ⅓, ⅔, ⅕, ⅖, ⅗, ⅘, ⅙, ⅚,
⅛, ⅜, ⅝, ⅞. Only ½, ¼, ¾ are in WinAnsi, and to keep the look consistent
the script normalises all of them.

If you want unicode fractions to render as native glyphs instead, register
a TTF font that covers the Number Forms block (DejaVu Sans is the
easiest), comment out the call to `normalize_text(...)`, and set the
font name on every ParagraphStyle.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import string
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

VULGAR_FRACTIONS = {
    "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4",
    "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5",
    "⅙": "1/6", "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}


def normalize_text(s: str) -> str:
    """Replace vulgar fractions with slash form. Mixed numbers ('2⅓') get
    a space between the whole part and the fraction so they read as
    '2 1/3' rather than '21/3'."""
    for vf, repl in VULGAR_FRACTIONS.items():
        s = re.sub(r"(\d)" + re.escape(vf), r"\1 " + repl, s)
    for vf, repl in VULGAR_FRACTIONS.items():
        s = s.replace(vf, repl)
    return s


def safe(s: str) -> str:
    """Escape for reportlab Paragraph and normalise fractions."""
    return escape(normalize_text(s))


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def make_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "QuizTitle", parent=base["Title"],
            fontSize=16, leading=20, alignment=TA_CENTER, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "QuizSubtitle", parent=base["Normal"],
            fontSize=10, leading=12, alignment=TA_CENTER,
            textColor=colors.grey, spaceAfter=12,
        ),
        "instructions": ParagraphStyle(
            "Instructions", parent=base["Normal"],
            fontSize=10, leading=13, spaceBefore=6, spaceAfter=10,
            textColor=colors.darkgrey,
        ),
        "stem": ParagraphStyle(
            "Stem", parent=base["Normal"],
            fontSize=11, leading=15, spaceBefore=6, spaceAfter=4,
        ),
        "option": ParagraphStyle(
            "Option", parent=base["Normal"],
            fontSize=10.5, leading=14, leftIndent=14,
        ),
        "section": ParagraphStyle(
            "Section", parent=base["Heading2"],
            fontSize=11, leading=14, spaceBefore=12, spaceAfter=6,
            textColor=colors.darkgrey,
        ),
        "footer": ParagraphStyle(
            "Footer", parent=base["Normal"],
            fontSize=9, alignment=TA_CENTER, textColor=colors.grey,
        ),
        "answer_row": ParagraphStyle(
            "AnswerRow", parent=base["Normal"],
            fontSize=10, leading=13,
        ),
    }


# ---------------------------------------------------------------------------
# Bank loading and selection
# ---------------------------------------------------------------------------

def load_bank(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_item(item: dict) -> dict:
    """Normalise an item to the legacy shape the rest of this script
    expects: `options` is a list of strings and `correct_answer` is the
    string value of the correct option.

    Accepts two input formats:

      Legacy (hand-authored v1 bank):
        {"options": ["12", "144", "132", "140"], "correct_answer": "144"}

      Dict (bank_generator.py output):
        {"options": {"A": "12", "B": "144", "C": "132", "D": "140"},
         "correct_option": "B"}

    Dict options are flattened by sorted key (A, B, C, D), so the visible
    letter order on the printed sheet matches the JSON. Also strips
    `target_nodes` when it's null (the v2 single-skill convention) so the
    key renderer's `", ".join(...)` doesn't crash on None.
    """
    new_item = dict(item)
    opts = item.get("options")

    if isinstance(opts, dict):
        letters = sorted(opts.keys())                  # ['A','B','C','D']
        new_item["options"] = [opts[L] for L in letters]
        correct_letter = item.get("correct_option")
        if correct_letter is None or correct_letter not in opts:
            raise ValueError(
                f"Item {item.get('item_id', '?')!r}: dict-form options but "
                f"correct_option is missing or invalid ({correct_letter!r})"
            )
        new_item["correct_answer"] = opts[correct_letter]
    elif isinstance(opts, list):
        if "correct_answer" not in item:
            raise ValueError(
                f"Item {item.get('item_id', '?')!r}: list-form options but "
                f"no correct_answer field"
            )
    else:
        raise ValueError(
            f"Item {item.get('item_id', '?')!r}: unsupported options type "
            f"{type(opts).__name__}"
        )

    # bank_generator.py writes target_nodes=null on single-skill items.
    # The answer-key renderer does ", ".join(item["target_nodes"]) which
    # would raise on None; drop the field so the `if "target_nodes" in item`
    # branch falls through to target_node.
    if new_item.get("target_nodes") is None:
        new_item.pop("target_nodes", None)

    return new_item


def select_items(bank: dict, include: Iterable[str]) -> list[dict]:
    """Concatenate the requested categories in order, normalising each
    item so downstream code sees a single canonical shape."""
    items: list[dict] = []
    for key in include:
        for raw in bank.get(key, []):
            items.append(normalize_item(raw))
    return items


def shuffle_options(item: dict, rng: random.Random) -> dict:
    """Return a copy of `item` with `options` reordered. `correct_answer`
    still matches one of the options (we shuffle the strings themselves)."""
    new = dict(item)
    new["options"] = list(item["options"])
    rng.shuffle(new["options"])
    return new


# ---------------------------------------------------------------------------
# Header and per-item layout
# ---------------------------------------------------------------------------

def header_flowables(styles: dict, meta: dict, form_id: str | None) -> list:
    subject = meta.get("subject", "Diagnostic Assessment")
    curriculum = meta.get("curriculum_context", "")

    flow = [
        Paragraph(f"{subject} — Diagnostic Assessment", styles["title"]),
    ]
    if curriculum:
        flow.append(Paragraph(escape(curriculum), styles["subtitle"]))

    info_table = Table(
        [
            ["Name:", "", "Class:", ""],
            ["School:", "", "Date:", ""],
        ],
        colWidths=[1.7 * cm, 8.3 * cm, 1.7 * cm, 5.3 * cm],
    )
    info_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (1, 0), (1, 0), 0.5, colors.black),
        ("LINEBELOW", (3, 0), (3, 0), 0.5, colors.black),
        ("LINEBELOW", (1, 1), (1, 1), 0.5, colors.black),
        ("LINEBELOW", (3, 1), (3, 1), 0.5, colors.black),
    ]))
    flow.append(info_table)

    instruction_text = (
        "Answer every question. For each item, <b>circle the letter</b> of "
        "the best answer. Working may be shown on the back of the page."
    )
    flow.append(Paragraph(instruction_text, styles["instructions"]))
    if form_id:
        flow.append(Paragraph(
            f"<font color='grey' size='8'>Form ID: {escape(form_id)}</font>",
            styles["answer_row"],
        ))
    flow.append(Spacer(1, 4))
    return flow


def options_table(options: list[str], styles: dict) -> Table:
    """Lay options out in a two-column grid (A, B / C, D)."""
    letters = list(string.ascii_uppercase)
    cells = [
        Paragraph(f"<b>{letter}.</b>  {safe(opt)}", styles["option"])
        for letter, opt in zip(letters, options)
    ]
    rows = []
    for i in range(0, len(cells), 2):
        left = cells[i]
        right = cells[i + 1] if i + 1 < len(cells) else ""
        rows.append([left, right])
    table = Table(rows, colWidths=[8.5 * cm, 8.5 * cm])
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def item_flowables(idx: int, item: dict, styles: dict) -> list:
    stem = Paragraph(
        f"<b>{idx}.</b>  {safe(item['stem'])}", styles["stem"],
    )
    return [KeepTogether([stem, options_table(item["options"], styles), Spacer(1, 6)])]


# ---------------------------------------------------------------------------
# Page decorations
# ---------------------------------------------------------------------------

def page_decorator(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillGray(0.5)
    canvas.drawCentredString(A4[0] / 2, 1.0 * cm, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Question sheet
# ---------------------------------------------------------------------------

def build_quiz_pdf(
    items: list[dict],
    meta: dict,
    output_path: Path,
    form_id: str | None = None,
) -> None:
    styles = make_styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=1.5 * cm,
        title="Diagnostic Assessment",
    )
    flow: list = []
    flow.extend(header_flowables(styles, meta, form_id))
    for idx, item in enumerate(items, start=1):
        flow.extend(item_flowables(idx, item, styles))
    doc.build(flow, onFirstPage=page_decorator, onLaterPages=page_decorator)


# ---------------------------------------------------------------------------
# Answer key
# ---------------------------------------------------------------------------

def build_answer_key_pdf(
    items: list[dict],
    meta: dict,
    output_path: Path,
    form_id: str | None = None,
) -> None:
    styles = make_styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=1.5 * cm,
        title="Diagnostic Assessment — Answer Key",
    )

    flow: list = [
        Paragraph(
            f"{meta.get('subject', 'Diagnostic Assessment')} — Answer Key",
            styles["title"],
        ),
    ]
    if form_id:
        flow.append(Paragraph(
            f"Form ID: {escape(form_id)}", styles["subtitle"],
        ))
    flow.append(Spacer(1, 6))

    letters = list(string.ascii_uppercase)
    rows = [["#", "Item ID", "Correct", "Target node(s)", "q_vector"]]
    for idx, item in enumerate(items, start=1):
        try:
            correct_letter = letters[item["options"].index(item["correct_answer"])]
        except ValueError:
            correct_letter = "?"
        if "target_nodes" in item:
            target = ", ".join(item["target_nodes"])
        else:
            target = item.get("target_node", "")
        q_vec_str = "".join(str(b) for b in item.get("q_vector", []))
        rows.append([
            str(idx),
            item.get("item_id", ""),
            f"{correct_letter}  ({safe(item['correct_answer'])})",
            safe(target),
            q_vec_str,
        ])

    table = Table(
        rows,
        colWidths=[0.8 * cm, 2.6 * cm, 3.6 * cm, 6.0 * cm, 3.4 * cm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    flow.append(table)

    flow.append(Spacer(1, 18))
    flow.append(Paragraph(
        "<b>Node order in q_vector:</b> " + escape(", ".join(meta.get("node_order", []))),
        styles["answer_row"],
    ))
    flow.append(Spacer(1, 8))
    flow.append(Paragraph(
        "<b>How to score:</b> mark each item right or wrong, build a "
        "response vector across all items in order, then run NPC or "
        "DINA-MLE classification against the feasible knowledge states "
        "derived from the DAG.",
        styles["answer_row"],
    ))

    doc.build(flow, onFirstPage=page_decorator, onLaterPages=page_decorator)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("bank_path", type=Path, help="Path to item bank JSON.")
    parser.add_argument(
        "--output", type=Path, default=Path("quiz.pdf"),
        help="Output path for the question sheet PDF (default: quiz.pdf).",
    )
    parser.add_argument(
        "--key-output", type=Path, default=None,
        help="Output path for the answer-key PDF (default: <output stem>_key.pdf).",
    )
    parser.add_argument(
        "--include", nargs="+",
        default=["single_skill_items", "multi_skill_items"],
        help="Bank categories to include in the form (default: both).",
    )
    parser.add_argument("--shuffle-items", action="store_true", help="Randomise item order.")
    parser.add_argument("--shuffle-options", action="store_true", help="Randomise option order within items.")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility.")
    parser.add_argument("--form-id", type=str, default=None, help="Label printed in the header (e.g. 'B6-2026-A').")
    args = parser.parse_args(argv)

    bank = load_bank(args.bank_path)
    meta = bank.get("meta", {})
    items = select_items(bank, args.include)

    if not items:
        parser.error(f"No items found for categories {args.include}.")

    rng = random.Random(args.seed)
    if args.shuffle_items:
        rng.shuffle(items)
    if args.shuffle_options:
        items = [shuffle_options(item, rng) for item in items]

    build_quiz_pdf(items, meta, args.output, form_id=args.form_id)

    key_path = args.key_output or args.output.with_name(args.output.stem + "_key.pdf")
    build_answer_key_pdf(items, meta, key_path, form_id=args.form_id)

    print(f"Quiz written to: {args.output}")
    print(f"Key written to:  {key_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())