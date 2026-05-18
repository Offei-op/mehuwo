"""Recognition pipeline: scanned roster sheets -> results.xlsx.

Inputs:
  - roster_cells.json from score_sheet_template.generate_roster_pdf:
    defines the quiz, the page count, the per-page ArUco IDs, and the
    mm-from-top-left geometry of every (student, item) tick cell.
  - one or more scanned sheets (PNG / JPG / PDF). Multi-page scans and
    single-page-per-file scans are both supported. Pages may be scanned
    in any order -- each page carries its own block of four ArUco IDs
    ([4p, 4p+1, 4p+2, 4p+3]) so the pipeline self-identifies pages.

Output:
  - results.xlsx with columns student_idx, student_name, item_1..item_N,
    scored. scored=1 if the page covering that student was successfully
    rectified and processed; scored=0 otherwise (page missing from the
    scans, or its corner fiducials failed to detect). The scoring layer
    treats scored=0 students as NaN, not zero -- "we didn't read their
    sheet" is not the same as "they got everything wrong".

Method:
  1. For each scan page, detect DICT_4X4_50 ArUco markers. The detected
     IDs identify which roster page this is (page_idx = id // 4).
  2. Take the four outer corners of the fiducial box (TL marker's
     top-left, TR marker's top-right, BR marker's bottom-right, BL
     marker's bottom-left) and solve a perspective homography to a
     canonical mm-pixel canvas at PX_PER_MM resolution.
  3. For each (student, item) cell on this page, crop the rectified
     image at the cell's mm coordinates and classify it as ticked or
     blank using a fill-ratio heuristic on the central region (a margin
     is shrunk off each side to avoid grid lines poisoning the metric).
  4. Assemble the results dataframe.

The classifier is pluggable. The default HeuristicTickClassifier is a
fill-fraction threshold on Otsu-binarised crops; a CNN backend (intended
for messy scans where the heuristic is unreliable) can be slotted in by
subclassing TickClassifier and passing it to process_scans.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import cv2
import numpy as np
import pandas as pd


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

PX_PER_MM_DEFAULT = 8.0          # 8 px/mm -> A4 rectifies to 1680 x 2376 px
FILL_THRESHOLD_DEFAULT = 0.08    # darker-pixel fraction above which a cell is "ticked"
MARGIN_FRAC_DEFAULT = 0.20       # shrink each cell by this fraction on every side
PDF_RENDER_SCALE_DEFAULT = 2.0   # pypdfium2 render scale for PDF inputs (~144 DPI)


# -----------------------------------------------------------------------------
# Loading scans (image and PDF)
# -----------------------------------------------------------------------------

def _read_image_gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cv2 could not read {path} -- not an image or corrupted")
    return img


def _read_pdf_pages_gray(path: Path, scale: float) -> list[np.ndarray]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise RuntimeError(
            f"reading {path} requires pypdfium2 -- pip install pypdfium2, "
            f"or pre-convert the PDF to PNG/JPG and pass those instead"
        ) from e

    doc = pdfium.PdfDocument(str(path))
    pages = []
    for i in range(len(doc)):
        pil = doc[i].render(scale=scale).to_pil().convert("L")
        pages.append(np.array(pil))
    return pages


def load_scan_pages(scan_paths: Iterable[Path], pdf_scale: float = PDF_RENDER_SCALE_DEFAULT,
                    ) -> Iterator[tuple[str, np.ndarray]]:
    """Yield (source_label, grayscale_image) for each page in every scan.

    Image files yield one page; PDFs yield as many pages as they contain.
    source_label is e.g. 'scan_001.jpg' or 'scans.pdf[p2]' -- used in logs
    and debug dumps so problems can be traced back to a specific page.
    """
    for path in scan_paths:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            for i, page in enumerate(_read_pdf_pages_gray(path, pdf_scale)):
                yield f"{path.name}[p{i+1}]", page
        elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
            yield path.name, _read_image_gray(path)
        else:
            raise ValueError(
                f"unsupported scan format: {path} "
                f"(expected .pdf, .png, .jpg/.jpeg, .tif/.tiff, .bmp)"
            )


# -----------------------------------------------------------------------------
# ArUco fiducial detection + homography
# -----------------------------------------------------------------------------

# cv2.aruco.detectMarkers returns each marker's corners in TL, TR, BR, BL order
# (clockwise from top-left, in image coordinates). We map them to the outer
# corners of the fiducial box on the printed sheet.
_ROLES = {"tl": 0, "tr": 1, "br": 2, "bl": 3}   # role -> index into marker corners


@dataclass
class PageDetection:
    page_idx: int
    # 4x2 float32 array of outer corners (in pixel space), order tl, tr, br, bl
    src_quad: np.ndarray
    n_markers_found: int           # how many markers detected (4 means full)
    marker_ids_used: list[int]


def detect_page(
    gray: np.ndarray,
    fiducial_ids_per_page: dict[int, dict[str, int]],
) -> PageDetection | None:
    """Detect ArUco markers and identify which roster page this is.

    Returns None if no recognisable page can be assembled (no markers, or
    no page has all four markers detected). When fewer than four markers
    for the inferred page are detected, this returns None as well -- a
    three-marker homography would silently distort the cells.
    """
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, params)
    corners, ids, _rejected = detector.detectMarkers(gray)

    if ids is None or len(ids) == 0:
        return None

    flat_ids = ids.flatten().tolist()

    # Tally which roster page each detected marker belongs to (id // 4),
    # restricted to pages that actually exist in this quiz.
    valid_pages = set(fiducial_ids_per_page.keys())
    page_hits: dict[int, list[int]] = {}
    for i, marker_id in enumerate(flat_ids):
        candidate = marker_id // 4
        if candidate in valid_pages:
            page_hits.setdefault(candidate, []).append(i)

    if not page_hits:
        return None

    # Pick the page with the most detected markers; require all four.
    page_idx, hit_indices = max(page_hits.items(), key=lambda kv: len(kv[1]))
    if len(hit_indices) < 4:
        return None

    expected = fiducial_ids_per_page[page_idx]   # {"tl": ..., "tr": ..., ...}
    # Map detected markers by their role for this page.
    detected_for_page = {flat_ids[i]: corners[i][0] for i in hit_indices}
    src = np.zeros((4, 2), dtype=np.float32)
    for role, dst_slot in [("tl", 0), ("tr", 1), ("br", 2), ("bl", 3)]:
        expected_id = expected[role]
        if expected_id not in detected_for_page:
            return None
        marker_corners = detected_for_page[expected_id]   # (4, 2) float
        src[dst_slot] = marker_corners[_ROLES[role]]

    return PageDetection(
        page_idx=page_idx,
        src_quad=src,
        n_markers_found=len(hit_indices),
        marker_ids_used=[expected["tl"], expected["tr"], expected["br"], expected["bl"]],
    )


def rectify_to_mm_canvas(
    gray: np.ndarray,
    page: PageDetection,
    sheet_w_mm: float,
    sheet_h_mm: float,
    fiducial_margin_mm: float,
    px_per_mm: float,
) -> np.ndarray:
    """Warp the source image so that the four outer corners of the fiducial
    box land on the corresponding mm coordinates in a canonical canvas at
    px_per_mm resolution. Returns the warped grayscale image.
    """
    canvas_w = int(round(sheet_w_mm * px_per_mm))
    canvas_h = int(round(sheet_h_mm * px_per_mm))

    # Outer corners of the fiducial box in mm (top-left origin)
    m = fiducial_margin_mm
    dst = np.array([
        [m,                  m],                  # tl outer
        [sheet_w_mm - m,     m],                  # tr outer
        [sheet_w_mm - m,     sheet_h_mm - m],     # br outer
        [m,                  sheet_h_mm - m],     # bl outer
    ], dtype=np.float32) * px_per_mm

    H = cv2.getPerspectiveTransform(page.src_quad, dst)
    return cv2.warpPerspective(gray, H, (canvas_w, canvas_h),
                               flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT,
                               borderValue=255)


# -----------------------------------------------------------------------------
# Tick classification
# -----------------------------------------------------------------------------

class TickClassifier:
    """Abstract base. Subclass to plug in a CNN or other backend."""
    def classify(self, cell_gray: np.ndarray) -> tuple[bool, float]:
        raise NotImplementedError


class HeuristicTickClassifier(TickClassifier):
    """Fill-ratio threshold on Otsu-binarised crops.

    Pipeline per cell:
      1. shrink off `margin_frac` on each side (kills grid lines / borders)
      2. Otsu-threshold the central crop -> binary mask of "dark" pixels
      3. fill_fraction = dark pixels / total pixels
      4. ticked iff fill_fraction >= fill_threshold

    The Otsu step makes the classifier robust to lighting variation across
    the scanned page; only the *relative* darkness inside each cell matters.
    """

    def __init__(self, fill_threshold: float = FILL_THRESHOLD_DEFAULT,
                 margin_frac: float = MARGIN_FRAC_DEFAULT):
        if not 0.0 < fill_threshold < 1.0:
            raise ValueError(f"fill_threshold must be in (0,1), got {fill_threshold}")
        if not 0.0 <= margin_frac < 0.5:
            raise ValueError(f"margin_frac must be in [0,0.5), got {margin_frac}")
        self.fill_threshold = fill_threshold
        self.margin_frac = margin_frac

    def _central_crop(self, cell: np.ndarray) -> np.ndarray:
        h, w = cell.shape[:2]
        dy = int(round(h * self.margin_frac))
        dx = int(round(w * self.margin_frac))
        return cell[dy:h - dy, dx:w - dx] if dy < h - dy and dx < w - dx else cell

    def classify(self, cell_gray: np.ndarray) -> tuple[bool, float]:
        crop = self._central_crop(cell_gray)
        if crop.size == 0:
            return False, 0.0
        # Otsu returns the auto-chosen threshold; we want pixels darker than it.
        _, binarised = cv2.threshold(crop, 0, 255,
                                     cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        fill = float(binarised.sum() / 255.0 / binarised.size)
        return fill >= self.fill_threshold, fill


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------

@dataclass
class PageResult:
    page_idx: int
    source_label: str
    ticks: dict[tuple[int, int], int]    # (student_idx, item_idx) -> 0/1
    fills: dict[tuple[int, int], float]  # (student_idx, item_idx) -> fill fraction


def _crop_cell(rectified: np.ndarray, cell: dict, px_per_mm: float) -> np.ndarray:
    x0 = int(round(cell["x_mm"] * px_per_mm))
    y0 = int(round(cell["y_mm"] * px_per_mm))
    x1 = int(round((cell["x_mm"] + cell["w_mm"]) * px_per_mm))
    y1 = int(round((cell["y_mm"] + cell["h_mm"]) * px_per_mm))
    h, w = rectified.shape[:2]
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    return rectified[y0:y1, x0:x1]


def _process_page(
    gray: np.ndarray,
    source_label: str,
    cells_by_page: dict[int, list[dict]],
    sheet_w_mm: float,
    sheet_h_mm: float,
    fiducial_margin_mm: float,
    fiducial_ids_per_page: dict[int, dict[str, int]],
    px_per_mm: float,
    classifier: TickClassifier,
    debug_dir: Path | None,
) -> PageResult | None:
    detection = detect_page(gray, fiducial_ids_per_page)
    if detection is None:
        return None

    rectified = rectify_to_mm_canvas(
        gray, detection, sheet_w_mm, sheet_h_mm,
        fiducial_margin_mm, px_per_mm,
    )
    page_cells = cells_by_page.get(detection.page_idx, [])
    ticks: dict[tuple[int, int], int] = {}
    fills: dict[tuple[int, int], float] = {}
    for cell in page_cells:
        crop = _crop_cell(rectified, cell, px_per_mm)
        is_ticked, fill = classifier.classify(crop)
        key = (cell["student_idx"], cell["item_idx"])
        ticks[key] = int(is_ticked)
        fills[key] = fill

    if debug_dir is not None:
        _dump_debug(debug_dir, source_label, detection, rectified,
                    page_cells, ticks, fills, px_per_mm)

    return PageResult(
        page_idx=detection.page_idx,
        source_label=source_label,
        ticks=ticks,
        fills=fills,
    )


def _dump_debug(
    debug_dir: Path, source_label: str, detection: PageDetection,
    rectified: np.ndarray, page_cells: list[dict],
    ticks: dict, fills: dict, px_per_mm: float,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    # Save rectified image with cells overlaid, green = ticked, red = blank.
    overlay = cv2.cvtColor(rectified, cv2.COLOR_GRAY2BGR)
    for cell in page_cells:
        x0 = int(round(cell["x_mm"] * px_per_mm))
        y0 = int(round(cell["y_mm"] * px_per_mm))
        x1 = int(round((cell["x_mm"] + cell["w_mm"]) * px_per_mm))
        y1 = int(round((cell["y_mm"] + cell["h_mm"]) * px_per_mm))
        is_ticked = ticks.get((cell["student_idx"], cell["item_idx"]), 0)
        colour = (0, 200, 0) if is_ticked else (0, 0, 200)
        cv2.rectangle(overlay, (x0, y0), (x1, y1), colour, 1)
    safe_name = source_label.replace("/", "_").replace("[", "_").replace("]", "")
    cv2.imwrite(str(debug_dir / f"page{detection.page_idx}_{safe_name}.png"), overlay)


def process_scans(
    cells_json: str | Path,
    scan_paths: list[str | Path],
    output_xlsx: str | Path,
    *,
    classifier: TickClassifier | None = None,
    px_per_mm: float = PX_PER_MM_DEFAULT,
    pdf_scale: float = PDF_RENDER_SCALE_DEFAULT,
    debug_dir: str | Path | None = None,
    expect_quiz_id: str | None = None,
) -> pd.DataFrame:
    """Run the full recognition pipeline and write results.xlsx.

    If expect_quiz_id is supplied, the cells.json quiz_id must match or
    the call raises. This is the metadata-misuse guardrail described in
    the project README (deterministic quiz_id printed on each sheet).
    """

    cells_meta = json.loads(Path(cells_json).read_text())
    if expect_quiz_id is not None and cells_meta["quiz_id"] != expect_quiz_id:
        raise ValueError(
            f"quiz_id mismatch: cells.json has '{cells_meta['quiz_id']}' "
            f"but --expect-quiz-id is '{expect_quiz_id}'. "
            f"You probably paired the wrong roster_cells.json with these scans."
        )

    sheet_w_mm = cells_meta["sheet_w_mm"]
    sheet_h_mm = cells_meta["sheet_h_mm"]
    fiducial_margin_mm = cells_meta["fiducial_margin_mm"]
    num_items = cells_meta["num_items"]
    n_students = cells_meta["n_students"]
    n_pages = cells_meta["n_pages"]
    fiducial_ids_per_page = {int(k): v for k, v in cells_meta["fiducial_ids_per_page"].items()}

    # Bucket cells by page for fast lookup during processing.
    cells_by_page: dict[int, list[dict]] = {}
    for cell in cells_meta["cells"]:
        cells_by_page.setdefault(cell["page_idx"], []).append(cell)
    # Recover the (student_idx -> student_name) mapping from any page.
    student_names: dict[int, str] = {}
    for cell in cells_meta["cells"]:
        student_names.setdefault(cell["student_idx"], cell["student_name"])

    classifier = classifier or HeuristicTickClassifier()
    debug_path = Path(debug_dir) if debug_dir is not None else None

    pages_processed: dict[int, PageResult] = {}    # page_idx -> result
    pages_failed: list[str] = []                    # source labels that didn't decode

    for source_label, gray in load_scan_pages([Path(p) for p in scan_paths], pdf_scale):
        result = _process_page(
            gray, source_label, cells_by_page,
            sheet_w_mm, sheet_h_mm, fiducial_margin_mm,
            fiducial_ids_per_page, px_per_mm, classifier, debug_path,
        )
        if result is None:
            pages_failed.append(source_label)
            print(f"  [skip] {source_label}: no usable ArUco quad detected")
            continue
        if result.page_idx in pages_processed:
            print(f"  [warn] {source_label}: page {result.page_idx} already "
                  f"processed from {pages_processed[result.page_idx].source_label}; "
                  f"overwriting with this one")
        pages_processed[result.page_idx] = result
        n_ticks = sum(result.ticks.values())
        print(f"  [ok]   {source_label}: page {result.page_idx} "
              f"({n_ticks} ticks across {len(result.ticks)} cells)")

    # Build the results dataframe -- every student gets a row, even if their
    # page wasn't in the scans. Missing-page students come through as
    # scored=0 with all item_* zeros (which the scoring layer converts to NaN).
    rows = []
    item_cols = [f"item_{i+1}" for i in range(num_items)]
    for student_idx in sorted(student_names):
        # Which page is this student on? Look it up from the cells.
        page_idx = None
        for p, cells in cells_by_page.items():
            if any(c["student_idx"] == student_idx for c in cells):
                page_idx = p
                break
        row = {
            "student_idx": student_idx,
            "student_name": student_names[student_idx],
        }
        scored = 0
        if page_idx is not None and page_idx in pages_processed:
            res = pages_processed[page_idx]
            scored = 1
            for i in range(num_items):
                row[item_cols[i]] = res.ticks.get((student_idx, i), 0)
        else:
            for i in range(num_items):
                row[item_cols[i]] = 0
        row["scored"] = scored
        rows.append(row)

    df = pd.DataFrame(rows, columns=["student_idx", "student_name", *item_cols, "scored"])

    out_path = Path(output_xlsx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Two-sheet xlsx: results + scan_meta (trace of what was processed).
    pages_missing = sorted(set(range(n_pages)) - set(pages_processed.keys()))
    meta_rows = [
        {"field": "quiz_id",                "value": cells_meta["quiz_id"]},
        {"field": "quiz_title",             "value": cells_meta.get("quiz_title", "")},
        {"field": "class_label",            "value": cells_meta.get("class_label", "")},
        {"field": "school_name",            "value": cells_meta.get("school_name", "")},
        {"field": "n_pages_total",          "value": n_pages},
        {"field": "n_pages_processed",      "value": len(pages_processed)},
        {"field": "pages_missing",          "value": ",".join(map(str, pages_missing)) or "(none)"},
        {"field": "scans_failed_to_decode", "value": ",".join(pages_failed)         or "(none)"},
        {"field": "n_students_total",       "value": n_students},
        {"field": "n_students_scored",      "value": int(df["scored"].sum())},
        {"field": "px_per_mm",              "value": px_per_mm},
        {"field": "classifier",             "value": type(classifier).__name__},
    ]
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="results", index=False)
        pd.DataFrame(meta_rows).to_excel(xw, sheet_name="scan_meta", index=False)

    print(f"\nwrote {out_path}")
    print(f"  scored {int(df['scored'].sum())}/{n_students} students  "
          f"({len(pages_processed)}/{n_pages} pages processed)")
    if pages_missing:
        print(f"  WARNING: pages {pages_missing} were not present in the scans; "
              f"their students come through as scored=0")
    if pages_failed:
        print(f"  WARNING: {len(pages_failed)} scan page(s) could not be decoded: "
              f"{pages_failed}")

    return df


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cells", required=True,
                    help="roster_cells.json produced by score_sheet_template")
    ap.add_argument("--scans", required=True, nargs="+",
                    help="one or more scanned-sheet files (.pdf, .png, .jpg, .tif, .bmp)")
    ap.add_argument("--output", required=True,
                    help="path to write results.xlsx")
    ap.add_argument("--debug-dir", default=None,
                    help="if set, dump rectified pages with cell overlays here")
    ap.add_argument("--fill-threshold", type=float, default=FILL_THRESHOLD_DEFAULT,
                    help=f"dark-fraction over which a cell counts as ticked "
                         f"(default {FILL_THRESHOLD_DEFAULT})")
    ap.add_argument("--margin-frac", type=float, default=MARGIN_FRAC_DEFAULT,
                    help=f"central-crop margin per side, as a fraction of cell size "
                         f"(default {MARGIN_FRAC_DEFAULT})")
    ap.add_argument("--px-per-mm", type=float, default=PX_PER_MM_DEFAULT,
                    help=f"resolution of the rectified canvas (default {PX_PER_MM_DEFAULT})")
    ap.add_argument("--pdf-scale", type=float, default=PDF_RENDER_SCALE_DEFAULT,
                    help=f"pypdfium2 render scale for PDF scans -- 2.0 ~= 144 DPI "
                         f"(default {PDF_RENDER_SCALE_DEFAULT})")
    ap.add_argument("--expect-quiz-id", default=None,
                    help="if set, error out unless cells.json has this quiz_id "
                         "(guard against feeding the wrong cells file)")
    args = ap.parse_args()

    classifier = HeuristicTickClassifier(
        fill_threshold=args.fill_threshold,
        margin_frac=args.margin_frac,
    )
    process_scans(
        cells_json=args.cells,
        scan_paths=args.scans,
        output_xlsx=args.output,
        classifier=classifier,
        px_per_mm=args.px_per_mm,
        pdf_scale=args.pdf_scale,
        debug_dir=args.debug_dir,
        expect_quiz_id=args.expect_quiz_id,
    )


if __name__ == "__main__":
    _main()
