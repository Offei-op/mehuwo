"""Page-image transcription review before curriculum text enters generation."""
import hashlib
import json

import streamlit as st

from assessment import jobs
from assessment.curriculum_ocr import engine, page_numbers, pdf_page, save_reviewed
from assessment.storage import atomic_bytes
from lib.jobs import queue, verified_result


def render_ocr(w):
    with st.expander("Scanned PDF transcription"):
        st.caption("Choose a small unit. Review every selected page against its image, especially numbers, symbols and table order. OCR confidence is not a correctness score.")
        try:
            engine()
            available = True
        except ValueError as exc:
            available = False
            st.info(str(exc))
        upload = st.file_uploader("Scanned or mixed PDF", type=["pdf"], key="ocr_pdf")
        pages_text = st.text_input("Physical PDF pages", "1", help="For example: 3-5,8. Maximum 20 pages per run.")
        language = st.text_input("OCR language codes", "eng")
        if st.button("Run OCR in background", disabled=not available or upload is None):
            try:
                data = upload.getvalue()
                pages = page_numbers(pages_text)
                pdf_page(data, max(pages))
                path = w.root / "curriculum_uploads" / f"{hashlib.sha256(data).hexdigest()}.pdf"
                atomic_bytes(path, data)
                queue(w, "ocr", {"pages": pages, "language": language, "filename": upload.name}, {"pdf": path})
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        completed = [r for r in jobs.list_jobs(w.root) if r["kind"] == "ocr" and r["status"] == "succeeded"]
        mode = st.radio("Transcription source", ["Completed OCR", "Manual transcription"], horizontal=True)
        try:
            if mode == "Completed OCR":
                if not completed:
                    st.caption("No completed OCR runs yet. Refresh this page after the job finishes.")
                    return
                job_id = st.selectbox("OCR run", [r["id"] for r in completed])
                result = verified_result(w.root, job_id)
                request = jobs.request(w.root, job_id)
                directory = jobs.folder(w.root, job_id)
                data = (directory / "inputs" / request["inputs"]["pdf"]["file"]).read_bytes()
                filename = request["payload"]["filename"]
                extraction = json.loads((directory / result["extraction"]).read_text(encoding="utf-8"))
            else:
                if upload is None:
                    return
                data, filename = upload.getvalue(), upload.name
                pages = page_numbers(pages_text)
                extraction = {"document_sha256": hashlib.sha256(data).hexdigest(), "engine": "manual transcription",
                              "sections": [{"id": f"page_{n}", "label": f"PDF page {n}", "text": ""} for n in pages]}
            corrected = {}
            for section in extraction["sections"]:
                number = int(section["id"].split("_")[1])
                left, right = st.columns(2)
                with left:
                    st.image(pdf_page(data, number), caption=section["label"])
                with right:
                    corrected[section["id"]] = st.text_area(section["label"] + " reviewed text", section["text"], height=300,
                        key=f"ocr_text_{hashlib.sha256(data).hexdigest()}_{extraction['engine']}_{section['id']}_{section['text'][:20]}")
                    if section.get("mean_word_confidence") is not None:
                        st.caption(f"Mean OCR word confidence: {section['mean_word_confidence']:.1f}/100")
            reviewer = st.text_input("Transcription reviewer")
            confirmed = st.checkbox("I checked all selected page transcriptions against the images")
            if st.button("Save reviewed transcription as a new curriculum source"):
                source_id = save_reviewed(w.root, data, filename, extraction, corrected, reviewer, confirmed)
                st.success(f"Saved reviewed text version {source_id[:12]}. Choose it under Saved curriculum. Skill-map approval is still required.")
        except (ValueError, RuntimeError, OSError) as exc:
            st.error(str(exc))
