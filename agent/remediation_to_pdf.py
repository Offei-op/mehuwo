"""Render remediation_pack.json as printable per-cluster PDFs.

Consumes the JSON output of remediation_content.generate_remediation_pack
and produces either:

  - one combined PDF (default), each cluster starting on a new page; or
  - one PDF per cluster, if --split is passed.

Each cluster section contains, for every step in its skill path:

  - Header                 step number, skill name, depth band, current mastery
  - Explanation            ~100-word in-context paragraph
  - Worked example         problem, numbered solution steps, final answer
  - Practice (3 items)     stem + options A/B/C/D, correct answer ticked, hint
  - Common mistakes        2-3 named misconceptions with corrections

The remediation_content script can call build_remediation_pdf(...) directly
or shell out via the CLI. See the docstring at the bottom for the one-line
integration into remediation_content.py.

Usage:
    python remediation_to_pdf.py \\
        --pack remediation_pack.json \\
        --output remediation_pack.pdf
    # or
    python remediation_to_pdf.py --pack remediation_pack.json \\
        --output-dir remediation_pdfs --split
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

from pdf_fonts import register_unicode_font
from pdf_text import safe_para as _safe


# ---- styling --------------------------------------------------------------

def _styles() -> dict:
    font = register_unicode_font()
    font_b = f"{font}-Bold" if font != "Helvetica" else "Helvetica-Bold"
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=font_b,
            fontSize=22, leading=26,
            textColor=colors.HexColor("#0F3D5C"), spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName=font,
            fontSize=10.5, leading=13,
            textColor=colors.HexColor("#555555"), spaceAfter=12,
        ),
        "cluster_h": ParagraphStyle(
            "cluster_h", parent=base["Heading1"], fontName=font_b,
            fontSize=18, leading=22,
            textColor=colors.HexColor("#0F3D5C"), spaceBefore=2, spaceAfter=2,
        ),
        "cluster_sub": ParagraphStyle(
            "cluster_sub", parent=base["Normal"], fontName=font,
            fontSize=10, leading=13,
            textColor=colors.HexColor("#444444"), spaceAfter=10,
        ),
        "step_h": ParagraphStyle(
            "step_h", parent=base["Heading2"], fontName=font_b,
            fontSize=14, leading=18,
            textColor=colors.HexColor("#185FA5"), spaceBefore=14, spaceAfter=2,
        ),
        "step_sub": ParagraphStyle(
            "step_sub", parent=base["Normal"], fontName=font,
            fontSize=9, leading=12,
            textColor=colors.HexColor("#777777"), spaceAfter=8,
        ),
        "section_h": ParagraphStyle(
            "section_h", parent=base["Heading3"], fontName=font_b,
            fontSize=11, leading=14,
            textColor=colors.HexColor("#0F3D5C"), spaceBefore=8, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"], fontName=font,
            fontSize=10.5, leading=14, spaceAfter=4,
        ),
        "answer": ParagraphStyle(
            "answer", parent=base["BodyText"], fontName=font,
            fontSize=10.5, leading=14,
            textColor=colors.HexColor("#0A6B2C"),
        ),
        "hint": ParagraphStyle(
            "hint", parent=base["Normal"], fontName=font,
            fontSize=9.5, leading=12,
            textColor=colors.HexColor("#666666"), leftIndent=8,
        ),
        "mistake_name": ParagraphStyle(
            "mistake_name", parent=base["Normal"], fontName=font_b,
            fontSize=10.5, leading=13,
            textColor=colors.HexColor("#A23B2B"),
        ),
        "_font": font,
        "_font_b": font_b,
    }


def _render_step(step: dict, sty: dict) -> list:
    """One step = header + explanation + worked example + practice + mistakes.
    Returns a list of flowables."""
    out: list = []
    content = step["content"]
    skill = step.get("skill") or content.get("skill", "(unknown skill)")

    band_bits = []
    if step.get("band"):
        band_bits.append(f"band: {step['band']}")
    if step.get("depth") is not None:
        band_bits.append(f"depth: {step['depth']}")
    if step.get("centroid_mastery") is not None:
        band_bits.append(f"cluster mastery: {step['centroid_mastery']:.2f}")

    out.append(Paragraph(
        f"Step {step.get('step', '?')} — {_safe(skill)}", sty["step_h"]))
    if band_bits:
        out.append(Paragraph(" · ".join(band_bits), sty["step_sub"]))

    # Explanation
    out.append(Paragraph("Explanation", sty["section_h"]))
    out.append(Paragraph(_safe(content["explanation"]), sty["body"]))

    # Worked example -- keep together so it doesn't split awkwardly
    we = content["worked_example"]
    we_block: list = [
        Paragraph("Worked example", sty["section_h"]),
        Paragraph(f"<b>Problem.</b> {_safe(we['problem'])}", sty["body"]),
    ]
    steps_list = ListFlowable(
        [ListItem(Paragraph(_safe(s), sty["body"]), leftIndent=12)
         for s in we["solution_steps"]],
        bulletType="1", start="1", leftIndent=18, bulletFontSize=10,
    )
    we_block.append(steps_list)
    we_block.append(Paragraph(
        f"<b>Answer.</b> {_safe(we['final_answer'])}", sty["answer"]))
    out.append(KeepTogether(we_block))

    # Practice items
    out.append(Paragraph("Practice", sty["section_h"]))
    for i, item in enumerate(content["practice_items"], 1):
        opts = item["options"]
        correct = item.get("correct_option", "")
        rows = [[
            f"Q{i}.",
            Paragraph(_safe(item["stem"]), sty["body"]),
        ]]
        for letter in sorted(opts.keys()):
            tick = "  ✓" if letter == correct else ""
            txt = f"<b>{letter})</b> {_safe(opts[letter])}{tick}"
            rows.append(["", Paragraph(txt, sty["body"])])
        rows.append(["", Paragraph(
            f"<i>Hint.</i> {_safe(item['hint'])}", sty["hint"])])
        tbl = Table(rows, colWidths=[1*cm, 16*cm])
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))
        out.append(KeepTogether([tbl, Spacer(1, 3 * mm)]))

    # Common mistakes
    out.append(Paragraph("Common mistakes to watch for", sty["section_h"]))
    for m in content["common_mistakes"]:
        block = [
            Paragraph(f"<b>{_safe(m['name'])}</b>", sty["mistake_name"]),
            Paragraph(_safe(m["description"]), sty["body"]),
            Paragraph(
                f"<i>Correction.</i> {_safe(m['correction'])}", sty["body"]),
            Spacer(1, 2 * mm),
        ]
        out.append(KeepTogether(block))

    return out


# ---- cluster + full doc rendering -----------------------------------------

def _render_cluster(c_id: int, c: dict, subject: str, sty: dict) -> list:
    out = [
        Paragraph(
            f"Cluster {c_id} — {_safe(c.get('label', ''))}", sty["cluster_h"]),
    ]
    sub_bits = [f"{c.get('n_students', 0)} students"]
    if c.get("mean_mastery") is not None:
        sub_bits.append(f"mean mastery {c['mean_mastery']:.2f}")
    sub_bits.append(subject)
    out.append(Paragraph(" · ".join(sub_bits), sty["cluster_sub"]))

    if c.get("note"):
        out.append(Paragraph(f"<i>{_safe(c['note'])}</i>", sty["body"]))
        return out

    if not c.get("path"):
        out.append(Paragraph("(no remediation path)", sty["body"]))
        return out

    for step in c["path"]:
        out.extend(_render_step(step, sty))
    return out


def build_remediation_pdf(
    pack_json: str | Path,
    output_pdf: str | Path,
) -> Path:
    """Single combined PDF with one section per cluster."""
    pack = json.loads(Path(pack_json).read_text(encoding="utf-8"))
    subject = pack.get("subject", "Mathematics")

    out = Path(output_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)

    sty = _styles()
    doc = SimpleDocTemplate(
        str(out), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=f"{subject} remediation pack",
    )

    story = [
        Paragraph(f"{subject} — remediation pack", sty["title"]),
        Paragraph(
            f"{len(pack['clusters'])} clusters · "
            f"generated {date.today().isoformat()}",
            sty["subtitle"]),
    ]
    if pack.get("bank_quiz_context"):
        story.append(Paragraph(
            _safe(pack["bank_quiz_context"]), sty["body"]))
        story.append(Spacer(1, 6 * mm))

    # Glance table: cluster, n, label, # of steps, first skill
    rows = [["Cluster", "n", "Label", "Steps", "First skill"]]
    cluster_ids = sorted(pack["clusters"].keys(), key=lambda x: int(x))
    for c_id in cluster_ids:
        c = pack["clusters"][c_id]
        path = c.get("path") or []
        first = path[0]["skill"] if path else (c.get("note") or "—")
        rows.append([
            str(int(c_id)),
            str(c.get("n_students", "")),
            _safe(c.get("label", "")),
            str(len(path)) if path else "0",
            _safe(first),
        ])
    glance = Table(rows, colWidths=[1.6*cm, 1.2*cm, 3.5*cm, 1.5*cm, 9.2*cm])
    glance.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3D5C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), sty["_font_b"]),
        ("FONTNAME", (0, 1), (-1, -1), sty["_font"]),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F4F6F8")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(glance)

    # Sort clusters numerically (json keys may be strings)
    for c_id in cluster_ids:
        story.append(PageBreak())
        story.extend(_render_cluster(int(c_id), pack["clusters"][c_id],
                                     subject, sty))

    doc.build(story)
    print(f"wrote {out}")
    return out


def build_remediation_pdfs_split(
    pack_json: str | Path,
    output_dir: str | Path,
) -> list[Path]:
    """One PDF per cluster, for handing to different teachers."""
    pack = json.loads(Path(pack_json).read_text(encoding="utf-8"))
    subject = pack.get("subject", "Mathematics")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sty = _styles()
    written: list[Path] = []
    for c_id_str, c in sorted(pack["clusters"].items(), key=lambda kv: int(kv[0])):
        c_id = int(c_id_str)
        out = out_dir / f"cluster_{c_id}_remediation.pdf"
        doc = SimpleDocTemplate(
            str(out), pagesize=A4,
            leftMargin=2 * cm, rightMargin=2 * cm,
            topMargin=1.8 * cm, bottomMargin=1.8 * cm,
            title=f"{subject} remediation — cluster {c_id}",
        )
        story = [
            Paragraph(f"{subject} — remediation", sty["title"]),
            Paragraph(
                f"Cluster {c_id} · generated {date.today().isoformat()}",
                sty["subtitle"]),
        ]
        story.extend(_render_cluster(c_id, c, subject, sty))
        doc.build(story)
        written.append(out)
        print(f"wrote {out}")
    return written


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True,
                    help="path to remediation_pack.json")
    ap.add_argument("--output", default=None,
                    help="combined PDF output path (default mode)")
    ap.add_argument("--output-dir", default=None,
                    help="directory for split PDFs (with --split)")
    ap.add_argument("--split", action="store_true",
                    help="one PDF per cluster instead of one combined PDF")
    args = ap.parse_args()

    if args.split:
        if not args.output_dir:
            raise SystemExit("--output-dir is required with --split")
        build_remediation_pdfs_split(args.pack, args.output_dir)
    else:
        if not args.output:
            raise SystemExit("--output is required (or pass --split)")
        build_remediation_pdf(args.pack, args.output)


# ---- to wire this into remediation_content.py -----------------------------
# Add this import at the top of remediation_content.py:
#
#     from remediation_to_pdf import build_remediation_pdf
#
# Then add a --pdf flag in _main() and, after generate_remediation_pack(...),
# call:
#
#     if args.pdf:
#         build_remediation_pdf(args.output, args.pdf)
#
# Or auto-derive the path: pdf_path = Path(args.output).with_suffix(".pdf")
# and call unconditionally.

if __name__ == "__main__":
    _main()
