"""Local PDF OCR, followed by explicit human transcription review."""
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from assessment.contracts import digest, now
from assessment.curriculum_maps import database
from assessment.storage import atomic_bytes


def engine():
    candidate = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not candidate:
        candidate = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if not Path(candidate).is_file():
        raise ValueError("Tesseract is not installed or not found. Install its engine and language data, then set TESSERACT_CMD or PATH. Manual transcription is also available.")
    return str(candidate)


def pdf_page(data, page_number):
    import pypdfium2 as pdfium
    if not data or len(data) > 20 * 1024 * 1024:
        raise ValueError("Use a PDF up to 20 MB.")
    doc = pdfium.PdfDocument(data)
    try:
        if not 1 <= page_number <= len(doc) <= 250:
            raise ValueError("Choose a valid physical page in a PDF of at most 250 pages.")
        page = doc[page_number - 1]
        try:
            width, height = page.get_size()
            scale = min(3, (12_000_000 / max(1, width * height)) ** .5)
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()


def page_numbers(value):
    if not re.fullmatch(r"[0-9, \-]+", value):
        raise ValueError("Enter page numbers or ranges, such as 3-5,8.")
    numbers = set()
    for part in value.split(","):
        bounds = [int(v.strip()) for v in part.split("-")]
        if len(bounds) > 2 or min(bounds) < 1 or max(bounds) > 250 or bounds[-1] < bounds[0]:
            raise ValueError("Use ascending page ranges between 1 and 250.")
        numbers.update(range(bounds[0], bounds[-1] + 1))
    if not 1 <= len(numbers) <= 20:
        raise ValueError("Process 1–20 pages at a time.")
    return sorted(numbers)


def transcribe(data, pages, language="eng"):
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ValueError("Use Tesseract language codes, such as eng or eng+fra.")
    if not pages or len(pages) > 20 or len(set(pages)) != len(pages):
        raise ValueError("Select 1–20 distinct pages.")
    binary = engine()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15, creationflags=flags, check=True).stdout.splitlines()[0]
    sections = []
    with tempfile.TemporaryDirectory() as tmp:
        for number in pages:
            image = pdf_page(data, number)
            path = Path(tmp) / "page.png"
            image.save(path)
            response = subprocess.run([binary, str(path), "stdout", "-l", language, "tsv"],
                                      capture_output=True, text=True, encoding="utf-8", timeout=120,
                                      creationflags=flags, check=True)
            lines, confidence = {}, []
            for row in csv.DictReader(io.StringIO(response.stdout), delimiter="\t"):
                if row.get("level") != "5" or not row.get("text", "").strip():
                    continue
                key = tuple(row[k] for k in ("block_num", "par_num", "line_num"))
                lines.setdefault(key, []).append(row["text"])
                if float(row["conf"]) >= 0:
                    confidence.append(float(row["conf"]))
            sections.append({"id": f"page_{number}", "label": f"PDF page {number}",
                             "text": "\n".join(" ".join(words) for words in lines.values()),
                             "mean_word_confidence": sum(confidence)/len(confidence) if confidence else None})
    return {"document_sha256": hashlib.sha256(data).hexdigest(), "engine": version,
            "language": language, "render_scale_cap": 3, "render_pixel_limit": 12_000_000,
            "segmentation": "Tesseract default", "created_at": now(), "sections": sections}


def save_reviewed(root, data, filename, extraction, corrected, reviewer, confirmed):
    if not confirmed or not reviewer.strip():
        raise ValueError("A named reviewer must confirm the transcription against the page images.")
    if hashlib.sha256(data).hexdigest() != extraction["document_sha256"]:
        raise ValueError("Extraction belongs to a different PDF.")
    original = {s["id"]: s for s in extraction["sections"]}
    if not original or len(original) != len(extraction["sections"]) or set(corrected) != set(original):
        raise ValueError("Review every extracted page; selected page identities must match.")
    sections = []
    for sid, text in corrected.items():
        if not re.fullmatch(r"page_[1-9][0-9]*", sid) or not isinstance(text, str) or not text.strip():
            raise ValueError("Every selected page needs readable, reviewed text.")
        sections.append({"id": sid, "label": f"PDF page {int(sid.split('_')[1])}", "text": text.strip()})
    if sum(len(s["text"]) for s in sections) > 1_000_000:
        raise ValueError("Reviewed text exceeds the curriculum size limit.")
    pdf_page(data, max(int(s["id"].split("_")[1]) for s in sections))
    record = {"extraction": extraction, "sections": sections, "reviewer": reviewer.strip(), "reviewed_at": now()}
    source_id = digest(record)
    folder = Path(root) / "curriculum_sources"
    atomic_bytes(folder / f"{source_id}.pdf", data)
    atomic_bytes(folder / f"{source_id}.review.json", json.dumps(record, ensure_ascii=False, indent=2).encode())
    with database(root) as con:
        con.execute("INSERT INTO curriculum_sources VALUES(?,?,?,?)", (source_id, Path(filename).name,
                    json.dumps(sections, ensure_ascii=False), now()))
    return source_id
