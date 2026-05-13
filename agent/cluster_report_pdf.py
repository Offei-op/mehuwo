"""Render cluster_summary_with_narratives.xlsx as a printable PDF report.

Takes the xlsx output of cluster_narrative.generate_cluster_narratives plus
the item bank JSON, and writes a PDF with:

  - Cover page: subject, k, total students, generation date
  - One section per cluster:
      header (cluster id + pedagogical label)
      stats strip (n, mean mastery, weakest skills)
      per-skill mastery heat-strip (colour-coded centroid bar)
      three narrative blocks (understands / misunderstands / watchpoints)

Designed for printing and handing to a teacher. The xlsx remains the
machine-readable artefact; this is the human deliverable.

Usage:
    python cluster_report_pdf.py \\
        --cluster-summary cluster_summary_with_narratives.xlsx \\
        --bank bank.json \\
        --output cluster_report.pdf
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from pdf_fonts import register_unicode_font
from pdf_text import safe_para


# ---- styling --------------------------------------------------------------

def _styles() -> dict:
    font = register_unicode_font()
    font_b = f"{font}-Bold" if font != "Helvetica" else "Helvetica-Bold"
    base = getSampleStyleSheet()
    s = {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=font_b,
            fontSize=22, leading=26,
            textColor=colors.HexColor("#0F3D5C"), spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName=font,
            fontSize=11, leading=14,
            textColor=colors.HexColor("#555555"), spaceAfter=18,
        ),
        "cluster_h": ParagraphStyle(
            "cluster_h", parent=base["Heading1"], fontName=font_b,
            fontSize=18, leading=22,
            textColor=colors.HexColor("#0F3D5C"), spaceBefore=4, spaceAfter=4,
        ),
        "cluster_sub": ParagraphStyle(
            "cluster_sub", parent=base["Normal"], fontName=font,
            fontSize=10, leading=13,
            textColor=colors.HexColor("#444444"), spaceAfter=10,
        ),
        "section_h": ParagraphStyle(
            "section_h", parent=base["Heading3"], fontName=font_b,
            fontSize=12, leading=15,
            textColor=colors.HexColor("#185FA5"), spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"], fontName=font,
            fontSize=10.5, leading=14, spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontName=font,
            fontSize=8.5, leading=11,
            textColor=colors.HexColor("#666666"),
        ),
        "skill_lbl": ParagraphStyle(
            "skill_lbl", parent=base["Normal"], fontName=font,
            fontSize=8, leading=10,
        ),
        "_font": font,
        "_font_b": font_b,
    }
    return s


def _mastery_colour(v: float) -> colors.Color:
    """Red-yellow-green gradient for a value in [0, 1]."""
    v = max(0.0, min(1.0, float(v)))
    if v < 0.5:
        # red -> yellow
        t = v / 0.5
        r, g, b = 0.85, 0.20 + 0.70 * t, 0.20
    else:
        # yellow -> green
        t = (v - 0.5) / 0.5
        r, g, b = 0.85 - 0.55 * t, 0.75, 0.20 + 0.25 * t
    return colors.Color(r, g, b)


def _mastery_strip(skills: list[str], centroid: dict, sty: dict) -> Table:
    """Coloured cells, one per skill, with the centroid value printed in.
    A horizontal heatmap row that reads like a glance-bar."""
    header = [Paragraph(s.replace("_", " "), sty["skill_lbl"]) for s in skills]
    values = [Paragraph(f"{centroid[s]:.2f}", sty["skill_lbl"]) for s in skills]
    tbl = Table([header, values], colWidths=[(17 * cm) / len(skills)] * len(skills))
    style = [
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF3")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#AAAAAA")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAAAAA")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for j, s in enumerate(skills):
        style.append(("BACKGROUND", (j, 1), (j, 1), _mastery_colour(centroid[s])))
    tbl.setStyle(TableStyle(style))
    return tbl


# ---- builder --------------------------------------------------------------

def build_cluster_report_pdf(
    cluster_summary_xlsx: str | Path,
    item_bank_json: str | Path,
    output_pdf: str | Path,
) -> Path:
    summary = pd.read_excel(cluster_summary_xlsx)
    bank = json.loads(Path(item_bank_json).read_text(encoding="utf-8"))
    skills: list[str] = bank["meta"]["node_order"]
    subject = bank["meta"].get("subject", "Mathematics")
    grade = bank["meta"].get("grade_level", "")

    out = Path(output_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)

    sty = _styles()
    doc = SimpleDocTemplate(
        str(out), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"{subject} cluster diagnostic report",
    )
    story = []

    # ---- cover ----
    total = int(summary["size"].sum())
    story.append(Paragraph(
        f"{subject} — cluster diagnostic report", sty["title"]))
    sub = (f"{grade} · " if grade else "") + (
        f"{len(summary)} clusters · {total} students · "
        f"generated {date.today().isoformat()}"
    )
    story.append(Paragraph(sub, sty["subtitle"]))
    story.append(Paragraph(
        "Each cluster below groups students with similar mastery profiles. "
        "The heat-strip shows the cluster's average mastery on each skill "
        "(red = weak, green = strong). The three narrative blocks summarise "
        "what the cluster likely knows, likely misunderstands, and what a "
        "teacher should watch for during remediation.",
        sty["body"]))
    story.append(Spacer(1, 8 * mm))

    # Quick-scan summary table of all clusters
    rows = [["Cluster", "n", "Mean", "Label", "Weakest skills"]]
    for _, r in summary.iterrows():
        rows.append([
            str(int(r["cluster"])),
            str(int(r["size"])),
            f"{r['mean_mastery']:.2f}",
            r["label"],
            r["weakest_skills"] or "—",
        ])
    tbl = Table(rows, colWidths=[1.6*cm, 1.2*cm, 1.6*cm, 3*cm, 9.6*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3D5C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), sty["_font_b"]),
        ("FONTNAME", (0, 1), (-1, -1), sty["_font"]),
        ("ALIGN", (0, 0), (3, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F4F6F8")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)

    # ---- per-cluster pages ----
    for _, r in summary.iterrows():
        story.append(PageBreak())
        story.append(Paragraph(
            f"Cluster {int(r['cluster'])} — {r['label']}", sty["cluster_h"]))
        story.append(Paragraph(
            f"<b>{int(r['size'])}</b> students · "
            f"mean mastery <b>{r['mean_mastery']:.2f}</b> · "
            f"weakest: {r['weakest_skills'] or '—'}",
            sty["cluster_sub"]))

        centroid = {s: float(r[f"centroid_{s}"]) for s in skills}
        story.append(_mastery_strip(skills, centroid, sty))
        story.append(Spacer(1, 6 * mm))

        for heading, col in [
            ("What they likely understand", "likely_understands"),
            ("What they likely misunderstand", "likely_misunderstands"),
            ("Teaching watchpoints", "teaching_watchpoints"),
        ]:
            story.append(Paragraph(heading, sty["section_h"]))
            text = str(r.get(col, "")).strip() or "—"
            story.append(Paragraph(safe_para(text), sty["body"]))

    doc.build(story)
    print(f"wrote {out}")
    return out


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cluster-summary", required=True,
                    help="cluster_summary_with_narratives.xlsx")
    ap.add_argument("--bank", required=True, help="item bank JSON")
    ap.add_argument("--output", required=True, help="output PDF path")
    args = ap.parse_args()
    build_cluster_report_pdf(args.cluster_summary, args.bank, args.output)


if __name__ == "__main__":
    _main()
