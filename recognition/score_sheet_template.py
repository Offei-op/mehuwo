"""Generate the Mehuwo master roster sheet (A4) with ArUco fiducial corners.

One row per student, one tick column per item. Fiducials at all four
corners enable perspective rectification by the recognition pipeline.
Auto-paginates across multiple A4 pages when the class is large.

v2 change: every sheet now carries a quiz_id (auto-generated from the
hash of students + num_items + quiz_title if not supplied). The id is
printed on every page and included in the cell-metadata JSON so the
recognition pipeline can refuse to apply the wrong metadata to a sheet.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# A4
SHEET_W_MM, SHEET_H_MM = 210.0, 297.0

# Layout (mm)
MARGIN_MM         = 8.0
FIDUCIAL_SIZE_MM  = 15.0
HEADER_BLOCK_H_MM = 22.0
COL_HEADER_H_MM   = 8.0
NAME_COL_W_MM     = 50.0
IDX_COL_W_MM      = 8.0
MIN_ROW_H_MM      = 5.0   # below this, ticks become unreliable


@dataclass
class Cell:
    page_idx: int
    student_idx: int    # global, across all pages
    item_idx: int
    student_name: str
    x_mm: float         # top-left of cell, mm from sheet top-left
    y_mm: float
    w_mm: float
    h_mm: float


def _make_quiz_id(students: list[str], num_items: int, quiz_title: str) -> str:
    """Deterministic id from the inputs that define what's printed on the sheet.
    Same students + items + title -> same id. Truncated to 12 hex chars."""
    h = hashlib.sha256()
    payload = f"{quiz_title}|{num_items}|{'|'.join(students)}".encode()
    h.update(payload)
    return "q_" + h.hexdigest()[:12]


def _aruco_png(marker_id: int, px: int = 200) -> bytes:
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(d, marker_id, px)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError(f"aruco encode failed for id={marker_id}")
    return buf.tobytes()


def _draw_fiducials(c: canvas.Canvas, page_idx: int) -> dict[str, int]:
    """Each page gets a unique block of four IDs so recognition knows
    which page it's looking at: page k uses ids [4k, 4k+1, 4k+2, 4k+3]."""
    base = 4 * page_idx
    ids = {"tl": base, "tr": base + 1, "bl": base + 2, "br": base + 3}
    placements = [
        (ids["tl"], MARGIN_MM,                                 SHEET_H_MM - MARGIN_MM - FIDUCIAL_SIZE_MM),
        (ids["tr"], SHEET_W_MM - MARGIN_MM - FIDUCIAL_SIZE_MM, SHEET_H_MM - MARGIN_MM - FIDUCIAL_SIZE_MM),
        (ids["bl"], MARGIN_MM,                                 MARGIN_MM),
        (ids["br"], SHEET_W_MM - MARGIN_MM - FIDUCIAL_SIZE_MM, MARGIN_MM),
    ]
    for mid, x_mm, y_mm in placements:
        c.drawImage(
            ImageReader(io.BytesIO(_aruco_png(mid))),
            x_mm * mm, y_mm * mm,
            width=FIDUCIAL_SIZE_MM * mm, height=FIDUCIAL_SIZE_MM * mm,
        )
    return ids


def _draw_page(
    c: canvas.Canvas,
    page_idx: int,
    n_pages: int,
    students_on_page: list[tuple[int, str]],  # (global_student_idx, name)
    num_items: int,
    quiz_title: str,
    class_label: str,
    school_name: str,
    quiz_id: str,
) -> list[Cell]:

    _draw_fiducials(c, page_idx)

    # Table bounds in mm-from-bottom-left (reportlab native)
    table_top_mm    = SHEET_H_MM - MARGIN_MM - FIDUCIAL_SIZE_MM - 2.0
    table_bottom_mm = MARGIN_MM + FIDUCIAL_SIZE_MM + 2.0
    table_left_mm   = MARGIN_MM + FIDUCIAL_SIZE_MM + 2.0
    table_right_mm  = SHEET_W_MM - MARGIN_MM - FIDUCIAL_SIZE_MM - 2.0

    header_top_mm   = table_top_mm
    colhdr_top_mm   = header_top_mm - HEADER_BLOCK_H_MM
    colhdr_bot_mm   = colhdr_top_mm - COL_HEADER_H_MM
    body_top_mm     = colhdr_bot_mm
    body_bot_mm     = table_bottom_mm
    body_h_mm       = body_top_mm - body_bot_mm

    n_rows = len(students_on_page)
    row_h_mm = body_h_mm / n_rows

    item_left_mm = table_left_mm + IDX_COL_W_MM + NAME_COL_W_MM
    item_w_total = table_right_mm - item_left_mm
    col_w_mm = item_w_total / num_items

    if row_h_mm < MIN_ROW_H_MM or col_w_mm < 5.0:
        print(f"warning: small cells on page {page_idx + 1} "
              f"({col_w_mm:.1f}x{row_h_mm:.1f}mm)")

    to_pt = lambda x_mm: x_mm * mm

    # Header text
    c.setFont("Helvetica-Bold", 12)
    c.drawString(to_pt(table_left_mm + 2), to_pt(header_top_mm - 6), quiz_title)
    c.setFont("Helvetica", 9)
    meta = "   ".join(s for s in [school_name, class_label] if s)
    if meta:
        c.drawString(to_pt(table_left_mm + 2), to_pt(header_top_mm - 12), meta)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(to_pt(table_left_mm + 2), to_pt(header_top_mm - 18),
                 "Tick = correct.  Leave blank = wrong or unanswered.")

    # Right-side header: page number + quiz_id stacked
    c.setFont("Helvetica", 8)
    if n_pages > 1:
        c.drawRightString(to_pt(table_right_mm - 2), to_pt(header_top_mm - 6),
                          f"Page {page_idx + 1} of {n_pages}")
    c.setFont("Helvetica", 7)
    c.drawRightString(to_pt(table_right_mm - 2), to_pt(header_top_mm - 18),
                      f"quiz_id: {quiz_id}")

    # Column headers
    c.setFont("Helvetica-Bold", 8)
    text_y = colhdr_bot_mm + COL_HEADER_H_MM / 3
    c.drawCentredString(to_pt(table_left_mm + IDX_COL_W_MM / 2), to_pt(text_y), "#")
    c.drawString(to_pt(table_left_mm + IDX_COL_W_MM + 2), to_pt(text_y), "Student")
    for i in range(num_items):
        cx = item_left_mm + (i + 0.5) * col_w_mm
        c.drawCentredString(to_pt(cx), to_pt(text_y), str(i + 1))

    # Grid lines
    c.setLineWidth(0.4)
    c.line(to_pt(table_left_mm), to_pt(colhdr_top_mm),
           to_pt(table_right_mm), to_pt(colhdr_top_mm))
    c.line(to_pt(table_left_mm), to_pt(colhdr_bot_mm),
           to_pt(table_right_mm), to_pt(colhdr_bot_mm))
    for r in range(n_rows + 1):
        y_mm = body_top_mm - r * row_h_mm
        c.line(to_pt(table_left_mm), to_pt(y_mm),
               to_pt(table_right_mm), to_pt(y_mm))

    verticals = [table_left_mm,
                 table_left_mm + IDX_COL_W_MM,
                 table_left_mm + IDX_COL_W_MM + NAME_COL_W_MM]
    verticals += [item_left_mm + i * col_w_mm for i in range(1, num_items + 1)]
    for vx in verticals:
        c.line(to_pt(vx), to_pt(colhdr_top_mm), to_pt(vx), to_pt(body_bot_mm))

    # Body rows + cell metadata
    cells: list[Cell] = []
    c.setFont("Helvetica", 8)
    max_name_chars = max(6, int(NAME_COL_W_MM / 1.7))

    for r, (global_idx, name) in enumerate(students_on_page):
        row_top_mm = body_top_mm - r * row_h_mm
        row_mid_mm = row_top_mm - row_h_mm / 2

        c.drawCentredString(to_pt(table_left_mm + IDX_COL_W_MM / 2),
                            to_pt(row_mid_mm - 2),
                            str(global_idx + 1))
        display = name if len(name) <= max_name_chars else name[:max_name_chars - 1] + "..."
        c.drawString(to_pt(table_left_mm + IDX_COL_W_MM + 1.5),
                     to_pt(row_mid_mm - 2), display)

        # cells: record in mm-from-TOP-LEFT (natural for image processing)
        cell_y_top_from_top = SHEET_H_MM - row_top_mm
        for i in range(num_items):
            cells.append(Cell(
                page_idx=page_idx,
                student_idx=global_idx,
                item_idx=i,
                student_name=name,
                x_mm=item_left_mm + i * col_w_mm,
                y_mm=cell_y_top_from_top,
                w_mm=col_w_mm,
                h_mm=row_h_mm,
            ))

    return cells


def generate_roster_pdf(
    students: list[str],
    num_items: int,
    output_path: str | Path,
    *,
    quiz_title: str = "Mehuwo - Master Roster",
    class_label: str = "",
    school_name: str = "",
    quiz_id: str | None = None,
    cell_metadata_path: str | Path | None = None,
    students_per_page: int = 40,
) -> tuple[str, list[Cell]]:
    """Generate the master roster PDF.

    Returns (quiz_id, cells). The quiz_id is auto-generated from the hash of
    (quiz_title, num_items, students) when not supplied; pass an explicit
    string for stable ids across regenerations with the same inputs.
    """
    if not students:
        raise ValueError("students must be non-empty")
    if num_items < 1:
        raise ValueError("num_items must be >= 1")

    if quiz_id is None:
        quiz_id = _make_quiz_id(students, num_items, quiz_title)

    out = Path(output_path)
    c = canvas.Canvas(str(out), pagesize=A4)

    pages = [students[i:i + students_per_page]
             for i in range(0, len(students), students_per_page)]
    n_pages = len(pages)

    all_cells: list[Cell] = []
    for p_idx, page_students in enumerate(pages):
        global_offset = p_idx * students_per_page
        indexed = [(global_offset + j, name) for j, name in enumerate(page_students)]
        all_cells.extend(_draw_page(
            c, p_idx, n_pages, indexed, num_items,
            quiz_title, class_label, school_name, quiz_id,
        ))
        if p_idx < n_pages - 1:
            c.showPage()

    c.save()

    if cell_metadata_path is not None:
        Path(cell_metadata_path).write_text(json.dumps({
            "quiz_id": quiz_id,
            "quiz_title": quiz_title,
            "class_label": class_label,
            "school_name": school_name,
            "sheet_w_mm": SHEET_W_MM,
            "sheet_h_mm": SHEET_H_MM,
            "aruco_dict": "DICT_4X4_50",
            "fiducial_size_mm": FIDUCIAL_SIZE_MM,
            "fiducial_margin_mm": MARGIN_MM,
            "fiducial_ids_per_page": {
                str(p): {"tl": 4 * p, "tr": 4 * p + 1,
                         "bl": 4 * p + 2, "br": 4 * p + 3}
                for p in range(n_pages)
            },
            "n_pages": n_pages,
            "num_items": num_items,
            "n_students": len(students),
            "cells": [asdict(cell) for cell in all_cells],
        }, indent=2))

    return quiz_id, all_cells


if __name__ == "__main__":
    students = [f"Student {i+1:03d}" for i in range(120)]
    quiz_id, cells = generate_roster_pdf(
        students=students,
        num_items=26,
        output_path="roster_test.pdf",
        cell_metadata_path="roster_test_cells.json",
        quiz_title="JHS 2 Mathematics - Fractions Quiz",
        class_label="JHS 2A",
        school_name="Sample Public JHS",
        students_per_page=40,
    )
    n_pages = max(c.page_idx for c in cells) + 1
    print(f"wrote roster_test.pdf  quiz_id={quiz_id}  "
          f"{len(cells)} cells across {n_pages} page(s)")
