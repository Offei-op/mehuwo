"""Page 2 — Materials.

Generates printable materials for the class:
  - quiz.pdf and quiz_key.pdf via curriculum/make_quiz_pdf.py
  - roster.pdf + cells.json via recognition/score_sheet_template.py
    (called directly as a Python function since it doesn't have a CLI).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.state import init_state, require_ws, save_ws, header, REPO_ROOT, invalidate_after
from lib.runners import run_make_quiz_pdf


st.set_page_config(page_title="Materials — mehuwo", page_icon="📐",
                   layout="wide")
init_state()
w = require_ws()


def _count_items(bank_path: Path) -> int:
    """Total item count = single_skill + multi_skill."""
    bank = json.loads(Path(bank_path).read_text())
    return len(bank.get("single_skill_items", [])) + \
           len(bank.get("multi_skill_items", []))


header(
    "Materials",
    eyebrow="step 2 of 5",
    description=(
        "Print the quiz the class will sit and the master roster sheet "
        "the marker will tick. The roster sheet has ArUco fiducials at "
        "the corners so the OCR step can rectify it from a phone photo."
    ),
)


if not w.bank or not Path(w.bank).exists():
    st.warning("No item bank in this workspace. Build or load a bank on "
               "the **Topic & Items** page first.")
    st.stop()


tab_quiz, tab_roster = st.tabs(["1 · Quiz PDF", "2 · Class roster"])

from assessment.review import review_status
reviewed_bank = json.loads(Path(w.bank).read_text(encoding="utf-8"))
statuses = review_status(w.root, reviewed_bank)
approved = sum(s["status"] == "approved" for s in statuses)
st.caption(f"Expert review for this exact bank version: {approved}/{len(statuses)} targets approved.")
st.page_link("pages/6_Expert_Review.py", label="Review this bank before classroom use")


# =========================================================================
# TAB 1: Quiz + answer key
# =========================================================================

with tab_quiz:
    st.markdown(
        '<div class="eyebrow">quiz pdf</div>'
        '<h3 style="margin-top:0">Generate the quiz and answer key</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="lead">Two A4 PDFs: the student-facing quiz (no answers) '
        "and the marker's key (item ID, correct letter, target node, "
        "q-vector). Vulgar fractions like ⅓ ⅔ ⅖ are normalised to slash "
        "notation because the default font does not carry those glyphs."
        "</p>",
        unsafe_allow_html=True,
    )

    if w.quiz_pdf and Path(w.quiz_pdf).exists():
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(
                f'<span class="pill done">quiz ready</span> '
                f'<span class="kbd">{Path(w.quiz_pdf).name}</span>',
                unsafe_allow_html=True,
            )
            st.download_button(
                "Download quiz PDF",
                data=Path(w.quiz_pdf).read_bytes(),
                file_name=Path(w.quiz_pdf).name,
                mime="application/pdf",
                use_container_width=True,
            )
        with c2:
            if w.quiz_key_pdf and Path(w.quiz_key_pdf).exists():
                st.markdown(
                    f'<span class="pill done">key ready</span> '
                    f'<span class="kbd">{Path(w.quiz_key_pdf).name}</span>',
                    unsafe_allow_html=True,
                )
                st.download_button(
                    "Download answer key PDF",
                    data=Path(w.quiz_key_pdf).read_bytes(),
                    file_name=Path(w.quiz_key_pdf).name,
                    mime="application/pdf",
                    use_container_width=True,
                )

    st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)

    st.markdown("**Options**")
    c1, c2, c3 = st.columns(3)
    with c1:
        shuffle_items = st.checkbox(
            "Shuffle item order",
            help="Print different students different question orders to "
                 "reduce copying.",
        )
    with c2:
        shuffle_options = st.checkbox(
            "Shuffle A/B/C/D order",
            help="Shuffle the option letters within each item.",
        )
    with c3:
        seed = st.number_input("Seed (for shuffling)",
                               min_value=0, max_value=99999, value=42)

    if st.button("Generate quiz and key", type="primary"):
        import uuid
        material_dir = w.root / "materials" / str(uuid.uuid4())
        material_dir.mkdir(parents=True)
        quiz_path = material_dir / "quiz.pdf"
        key_path = material_dir / "quiz_key.pdf"

        with st.spinner("Rendering…"):
            log = []
            for stream, line in run_make_quiz_pdf(
                bank=Path(w.bank),
                output=quiz_path,
                key_output=key_path,
                shuffle_items=shuffle_items,
                shuffle_options=shuffle_options,
                seed=int(seed) if (shuffle_items or shuffle_options) else None,
            ):
                if stream == "exit":
                    rc = int(line)
                    if rc != 0:
                        st.error(f"Renderer exited with code {rc}")
                        st.code("\n".join(log) or "(no output)",
                                language="text")
                        st.stop()
                else:
                    log.append(line)

        invalidate_after(w, "quiz")
        w.quiz_pdf = quiz_path
        w.quiz_key_pdf = key_path
        w.quiz_manifest = quiz_path.with_suffix(".form.json")
        save_ws()
        st.success(f"Wrote {quiz_path.name} and {key_path.name}.")
        time.sleep(0.4)
        st.rerun()


# =========================================================================
# TAB 2: Roster sheet
# =========================================================================

with tab_roster:
    st.markdown(
        '<div class="eyebrow">class roster</div>'
        '<h3 style="margin-top:0">Build the marker\'s roster sheet</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="lead">One row per student, one tick column per item. '
        "ArUco fiducials at the four page corners let the OCR step "
        "rectify the sheet from a phone photo. A deterministic <span "
        "class='kbd'>quiz_id</span> is printed on every page so the "
        "wrong cells file can't be applied to the wrong scanned sheet."
        "</p>",
        unsafe_allow_html=True,
    )

    if w.roster_pdf and Path(w.roster_pdf).exists():
        try:
            cells = json.loads(Path(w.roster_cells).read_text())
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Students", cells.get("n_students", "?"))
            c2.metric("Items", cells.get("num_items", "?"))
            c3.metric("Pages", cells.get("n_pages", "?"))
            c4.metric("Quiz ID", cells.get("quiz_id", "—"))
        except Exception:
            pass

        st.markdown(
            f'<span class="pill done">roster ready</span> '
            f'<span class="kbd">{Path(w.roster_pdf).name}</span>',
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "Download roster PDF",
                data=Path(w.roster_pdf).read_bytes(),
                file_name=Path(w.roster_pdf).name,
                mime="application/pdf",
                use_container_width=True,
            )
        with c2:
            if w.roster_cells and Path(w.roster_cells).exists():
                st.download_button(
                    "Download cell metadata (JSON)",
                    data=Path(w.roster_cells).read_bytes(),
                    file_name=Path(w.roster_cells).name,
                    mime="application/json",
                    use_container_width=True,
                )

    st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)

    st.markdown("**Class details**")
    c1, c2 = st.columns(2)
    with c1:
        school_name = st.text_input("School name",
                                    value="Sample School")
        class_label = st.text_input("Class label", value="Class A")
    with c2:
        quiz_title = st.text_input(
            "Quiz title", value="Mathematics — Fractions Quiz",
        )
        students_per_page = st.number_input(
            "Students per A4 page",
            min_value=10, max_value=60, value=40,
            help="Below ~5 mm row height ticks become unreliable.",
        )

    st.markdown("**Student names**")
    names_method = st.radio(
        "Student names input",
        ["Generate placeholders", "Paste one per line"],
        horizontal=True, label_visibility="collapsed",
    )

    if names_method == "Generate placeholders":
        n_students = st.number_input("Class size",
                                     min_value=1, max_value=300, value=40)
        students = [f"Student {i+1:03d}" for i in range(n_students)]
    else:
        raw = st.text_area(
            "Student names (one per line)",
            value="Student 001\nStudent 002\nStudent 003\nStudent 004\n"
                  "Student 005\nStudent 006",
            height=180,
        )
        students = [s.strip() for s in raw.splitlines() if s.strip()]
        st.caption(f"{len(students)} student(s)")

    if st.button("Generate roster sheet", type="primary",
                 disabled=not students):
        # The roster generator is a library function, not a CLI, so we
        # call it directly. Push REPO_ROOT onto sys.path first.
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from recognition.score_sheet_template import generate_roster_pdf

        from assessment.contracts import digest
        if not w.quiz_manifest or not Path(w.quiz_manifest).exists():
            st.error("Generate the quiz first so the roster can use its question mapping.")
            st.stop()
        form = json.loads(Path(w.quiz_manifest).read_text(encoding="utf-8"))
        if form["bank_version"] != digest(json.loads(Path(w.bank).read_text(encoding="utf-8"))):
            st.error("The bank changed. Generate a new quiz before its roster.")
            st.stop()
        import uuid
        roster_dir = w.root / "materials" / str(uuid.uuid4())
        roster_dir.mkdir(parents=True)
        roster_path = roster_dir / "roster.pdf"
        cells_path = roster_dir / "roster_cells.json"

        with st.spinner("Drawing fiducials and tick cells…"):
            quiz_id, cells = generate_roster_pdf(
                students=students,
                num_items=_count_items(w.bank),
                output_path=str(roster_path),
                cell_metadata_path=str(cells_path),
                quiz_title=quiz_title,
                class_label=class_label,
                school_name=school_name,
                students_per_page=int(students_per_page),
                form_manifest=form,
            )

        invalidate_after(w, "results")
        w.results_xlsx = w.scan_draft = w.scan_context = None
        w.roster_pdf = roster_path
        w.roster_cells = cells_path
        save_ws()
        n_pages = max(c.page_idx for c in cells) + 1
        st.success(
            f"Wrote {roster_path.name} · quiz_id={quiz_id} · "
            f"{len(cells)} cells across {n_pages} page(s)."
        )
        time.sleep(0.4)
        st.rerun()
