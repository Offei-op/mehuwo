"""Recognition draft -> teacher correction -> canonical scoring input."""
import hashlib
import json
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from assessment.contracts import digest
from assessment.scan_review import approve_scan
from lib.state import invalidate_after, save_ws


def render_scan_review(w):
    if not w.roster_cells or not Path(w.roster_cells).exists():
        st.info("Generate a roster on Materials before uploading its scans.")
        return
    bank = json.loads(Path(w.bank).read_text(encoding="utf-8"))
    metadata = json.loads(Path(w.roster_cells).read_text(encoding="utf-8"))
    form = metadata.get("form")
    st.caption("The scanner reads correctness ticks, not written answers. "
               "ArUco markers identify pages; they do not verify the printed quiz ID.")
    st.write(f"Expected printed quiz ID: {metadata['quiz_id']}")
    confirmed = st.text_input("Enter the quiz ID printed on the scanned roster")
    legacy = False
    if not form:
        legacy = st.checkbox("This legacy roster uses the current bank's original question order")
    scans = st.file_uploader("Roster images or PDFs", type=["pdf", "png", "jpg", "jpeg", "tif", "tiff"],
                             accept_multiple_files=True, key="roster_scans")
    demo = st.checkbox("Use the workspace's printed roster as a recognition demonstration")
    if st.button("Recognize roster", disabled=not scans and not demo):
        if confirmed.strip() != metadata["quiz_id"] or (not form and not legacy):
            st.error("Confirm the printed quiz ID and the legacy question order if applicable.")
            return
        if form and form["bank_version"] != digest(bank):
            st.error("This roster belongs to a different bank version. Restore that bank or print new materials.")
            return
        run = w.root / "scans" / str(uuid.uuid4())
        run.mkdir(parents=True)
        paths = []
        if demo:
            if not w.roster_pdf or not Path(w.roster_pdf).exists():
                st.error("No printed roster is available.")
                return
            paths = [Path(w.roster_pdf)]
        else:
            for i, upload in enumerate(scans):
                path = run / f"scan_{i}{Path(upload.name).suffix.lower()}"
                path.write_bytes(upload.getvalue())
                paths.append(path)
        from recognition.recognition_pipeline import process_scans
        try:
            with st.spinner("Recognizing pages and extracting tick cells..."):
                process_scans(w.roster_cells, paths, run / "raw.xlsx", debug_dir=run / "debug",
                              expect_quiz_id=confirmed.strip())
        except Exception as exc:
            st.error(f"Recognition failed: {exc}")
            return
        context = {"bank_version": digest(bank), "form": form, "quiz_id": confirmed.strip(),
                   "sources": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
        (run / "context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
        w.scan_draft, w.scan_context = run / "raw.xlsx", run / "context.json"
        save_ws()
        st.rerun()
    if not w.scan_draft or not Path(w.scan_draft).exists():
        return
    context = json.loads(Path(w.scan_context).read_text(encoding="utf-8"))
    if context["bank_version"] != digest(bank):
        st.warning("The pending scan belongs to an older bank; it cannot be applied to this bank.")
        return
    raw = pd.read_excel(w.scan_draft)
    st.subheader("Review recognition before scoring")
    with st.expander("Page detection and scan metadata", expanded=True):
        st.dataframe(pd.read_excel(w.scan_draft, sheet_name="scan_meta"), hide_index=True)
    with st.expander("Rectified pages and tick-cell overlays"):
        for path in sorted((Path(w.scan_draft).parent / "debug").glob("*.png")):
            st.image(str(path), caption=path.name)
    st.caption("Columns follow printed question numbers here. Correct cells, and use blank "
               "for unknown results. Missing pages remain unscored until reviewed.")
    display = raw.copy()
    cols = [c for c in raw if str(c).startswith("item_")]
    display.loc[display.scored == 0, cols] = np.nan
    corrected = st.data_editor(display, disabled=["student_idx", "student_name"],
                               num_rows="fixed", key=f"scan_editor_{w.scan_draft}")
    reviewer = st.text_input("Scan reviewer")
    if st.button("Approve corrected scan for scoring"):
        try:
            result_path = approve_scan(w.root, raw, corrected, bank, reviewer=reviewer,
                                       form=context.get("form"), source=str(w.scan_context))
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
            return
        invalidate_after(w, "results")
        w.results_xlsx = result_path
        w.scan_draft = w.scan_context = None
        save_ws()
        st.success("Reviewed results saved with corrections and provenance.")
        st.rerun()
