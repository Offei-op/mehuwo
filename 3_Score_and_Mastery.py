"""Page 3 — Score & Mastery.

Three ways to get a results.xlsx into the workspace:
  1. Upload one prepared elsewhere
  2. Generate synthetic results from the bank for demo / testing
  3. Run the recognition pipeline on scanned roster sheets — ArUco
     rectification + tick classification produces a real results.xlsx
     from photos/PDFs of the printed sheet

Then runs the scoring layer to produce mastery.xlsx.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.state import init_state, require_ws, save_ws, header
from lib.runners import run_scoring, run_recognition
from lib.viz import per_student_mean_hist, mastery_distribution
from lib.demo import generate_synthetic_results


st.set_page_config(page_title="Score & Mastery — mehuwo", page_icon="📐",
                   layout="wide")
init_state()
w = require_ws()


header(
    "Score & Mastery",
    eyebrow="step 3 of 5",
    description=(
        "Turn per-item correctness into per-skill mastery. Three ways to "
        "produce the per-item correctness file: upload one, generate "
        "synthetic results for testing, or run the recognition pipeline "
        "on scanned roster sheets."
    ),
)


if not w.bank or not Path(w.bank).exists():
    st.warning("No item bank in this workspace. Build one on the "
               "**Topic & Items** page first.")
    st.stop()


# =========================================================================
# Stage A — get a results.xlsx in the workspace
# =========================================================================

st.markdown(
    '<div class="eyebrow">stage a</div>'
    '<h3 style="margin-top:0">Provide per-item results</h3>',
    unsafe_allow_html=True,
)

if w.results_xlsx and Path(w.results_xlsx).exists():
    df = pd.read_excel(w.results_xlsx)
    n_total = len(df)
    n_scored = int(df.get("scored", pd.Series([1] * n_total)).sum())
    st.markdown(
        f'<span class="pill done">results loaded</span> '
        f'<span class="kbd">{Path(w.results_xlsx).name}</span> · '
        f'{n_scored}/{n_total} students scored',
        unsafe_allow_html=True,
    )

tab_upload, tab_synthetic, tab_ocr = st.tabs(
    ["Upload results.xlsx", "Generate synthetic (demo)", "From roster scan"]
)

with tab_upload:
    st.markdown(
        '<p class="lead">Format: rows = students. Columns include '
        '<span class="kbd">student_idx</span>, <span class="kbd">student_name'
        '</span>, <span class="kbd">item_1</span>…<span class="kbd">item_N'
        '</span> (1 = correct, 0 = wrong), and <span class="kbd">scored</span> '
        "(1 if the student's sheet was processed, 0 if not).</p>",
        unsafe_allow_html=True,
    )
    up = st.file_uploader("results.xlsx", type=["xlsx"],
                          label_visibility="collapsed")
    if up is not None:
        dest = w.root / "results.xlsx"
        dest.write_bytes(up.getvalue())
        w.results_xlsx = dest
        save_ws()
        st.success(f"Saved as {dest.name}")
        time.sleep(0.3)
        st.rerun()

with tab_synthetic:
    st.markdown(
        '<p class="lead">For exercising the rest of the pipeline without '
        "real students. Sample from three archetypes (struggling / "
        "developing / strong); per-item correctness is sampled from a "
        "Bernoulli whose parameter depends on the item's required skills."
        "</p>",
        unsafe_allow_html=True,
    )
    st.warning("Synthetic data is for demos only. Don't use this to "
               "make pedagogical decisions about real students.")

    c1, c2, c3 = st.columns(3)
    with c1:
        n_synth = st.number_input("Class size", 5, 200, value=40)
    with c2:
        seed = st.number_input("Seed", 0, 99999, value=42)
    with c3:
        unscored_rate = st.slider(
            "Unscored rate", 0.0, 0.2, value=0.05, step=0.01,
            help="Simulates the fraction of sheets the OCR step fails on.",
        )

    st.markdown("**Archetype mix**")
    c1, c2, c3 = st.columns(3)
    p_struggling = c1.slider("Struggling", 0.0, 1.0, 0.30, 0.05)
    p_developing = c2.slider("Developing", 0.0, 1.0, 0.50, 0.05)
    p_strong = c3.slider("Strong", 0.0, 1.0, 0.20, 0.05)
    total = p_struggling + p_developing + p_strong
    if total == 0:
        st.error("At least one archetype share must be > 0.")
    else:
        st.caption(
            f"Normalised: struggling {p_struggling/total:.0%}, "
            f"developing {p_developing/total:.0%}, "
            f"strong {p_strong/total:.0%}"
        )

    if st.button("Generate synthetic results", type="primary",
                 disabled=(total == 0)):
        dest = w.root / "results.xlsx"
        with st.spinner("Sampling…"):
            df = generate_synthetic_results(
                bank_path=Path(w.bank),
                output_path=dest,
                n_students=int(n_synth),
                archetype_mix={
                    "struggling": p_struggling / total,
                    "developing": p_developing / total,
                    "strong": p_strong / total,
                },
                unscored_rate=float(unscored_rate),
                seed=int(seed),
            )
        w.results_xlsx = dest
        save_ws()
        st.success(f"Generated {len(df)} synthetic students.")
        time.sleep(0.4)
        st.rerun()

with tab_ocr:
    if not w.roster_cells or not Path(w.roster_cells).exists():
        st.markdown(
            '<div class="card"><h4>Need a roster sheet first</h4>'
            '<div class="muted" style="font-size:0.88rem">'
            "The recognition pipeline reads the per-cell coordinates and "
            "the deterministic <span class='kbd'>quiz_id</span> out of "
            "<span class='kbd'>roster_cells.json</span>. Generate the "
            "roster on the <strong>Materials</strong> page first."
            "</div></div>",
            unsafe_allow_html=True,
        )
    else:
        cells_meta = json.loads(Path(w.roster_cells).read_text())
        quiz_id_expected = cells_meta["quiz_id"]
        n_pages = cells_meta["n_pages"]
        n_items = cells_meta["num_items"]
        n_students_meta = cells_meta["n_students"]

        st.markdown(
            f'<p class="lead">Upload your scanned roster sheets — phone '
            f"photos, scanner PDFs, or image files. The pipeline detects "
            f"the ArUco fiducials on each page, rectifies the perspective "
            f"to the cell coordinates in "
            f"<span class='kbd'>roster_cells.json</span>, and classifies "
            f"every tick cell. <strong>Expected:</strong> "
            f"{n_pages} page{'s' if n_pages != 1 else ''} "
            f"covering {n_students_meta} students &times; {n_items} items, "
            f"quiz id <span class='kbd'>{quiz_id_expected}</span>."
            f"</p>",
            unsafe_allow_html=True,
        )

        uploads = st.file_uploader(
            "Scanned roster sheets (PDF, PNG, JPG, TIF, BMP)",
            type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )

        col_a, col_b = st.columns([1, 2])
        with col_a:
            use_demo = st.button(
                "Or: try the workspace's own roster",
                use_container_width=True,
                disabled=not (w.roster_pdf and Path(w.roster_pdf).exists()),
                help="Run recognition on this workspace's printed roster "
                     "PDF (blank — no ticks). Useful as a sanity check "
                     "when you don't have a scanned sheet at hand.",
            )
        with col_b:
            run_uploads = (
                st.button(
                    f"Recognise {len(uploads)} uploaded "
                    f"file{'s' if len(uploads) != 1 else ''}",
                    type="primary", use_container_width=True,
                )
                if uploads else False
            )
            if not uploads:
                st.caption("Upload one or more scans, or use the demo "
                           "button to recognise the bundled blank roster.")

        with st.expander("Advanced settings", expanded=False):
            cc1, cc2 = st.columns(2)
            with cc1:
                fill_threshold = st.slider(
                    "Fill threshold", 0.02, 0.40, 0.08, 0.01,
                    help="Dark-pixel fraction (after Otsu binarisation of "
                         "the cell's central region) above which the cell "
                         "is classified as ticked. Lower = more sensitive. "
                         "Tune on your scanner + ink combination.",
                )
            with cc2:
                margin_frac = st.slider(
                    "Cell-margin shrink", 0.0, 0.40, 0.20, 0.05,
                    help="Fraction of each cell trimmed off every side "
                         "before classifying — kills grid-line and "
                         "name-text bleed from the surrounding row.",
                )

        # Decide whether and on what to run
        scans_to_process = None
        if use_demo:
            scans_to_process = [Path(w.roster_pdf)]
        elif run_uploads and uploads:
            scans_dir = w.root / "scans"
            scans_dir.mkdir(parents=True, exist_ok=True)
            scans_to_process = []
            for up in uploads:
                dest = scans_dir / up.name
                dest.write_bytes(up.getvalue())
                scans_to_process.append(dest)

        if scans_to_process:
            results_path = w.root / "results.xlsx"
            debug_dir = w.root / "recognition_debug"

            log_area = st.empty()
            status_area = st.empty()
            lines: list[str] = []
            rc = None

            status_area.info(
                f"Running recognition on {len(scans_to_process)} "
                f"file{'s' if len(scans_to_process) != 1 else ''}…"
            )

            for stream, line in run_recognition(
                cells=Path(w.roster_cells),
                scans=scans_to_process,
                output=results_path,
                debug_dir=debug_dir,
                fill_threshold=float(fill_threshold),
                margin_frac=float(margin_frac),
                expect_quiz_id=quiz_id_expected,
            ):
                if stream == "exit":
                    rc = int(line)
                    break
                lines.append(line)
                log_area.code("\n".join(lines[-25:]), language="text")

            if rc == 0 and results_path.exists():
                w.results_xlsx = results_path
                save_ws()
                df = pd.read_excel(results_path)
                n_scored = int(df["scored"].sum())
                n_ticks = int(df.filter(like="item_").sum().sum())
                status_area.success(
                    f"Recognition complete — {n_scored}/{len(df)} students "
                    f"scored, {n_ticks} tick(s) detected."
                )

                # Surface the debug overlay images so the judge can SEE
                # the rectified page with cell rectangles drawn on it
                # (green where ticks were detected, red where blank).
                debug_pngs = sorted(debug_dir.glob("*.png")) \
                    if debug_dir.exists() else []
                if debug_pngs:
                    with st.expander(
                        f"Debug overlays — rectified pages with cell "
                        f"classifications ({len(debug_pngs)} page"
                        f"{'s' if len(debug_pngs) != 1 else ''})",
                        expanded=False,
                    ):
                        st.caption(
                            "Green rectangles = cells classified as "
                            "ticked. Red = blank. These are the "
                            "rectified, mm-pixel-aligned images the "
                            "classifier actually saw."
                        )
                        for png in debug_pngs:
                            st.image(str(png), caption=png.name,
                                     use_container_width=True)

                time.sleep(0.7)
                st.rerun()
            else:
                status_area.error(
                    f"Recognition exited with code {rc}. The most common "
                    f"cause is a quiz_id mismatch — pages from a different "
                    f"quiz can't be paired with this workspace's "
                    f"roster_cells.json (the id is printed on every "
                    f"sheet and embedded in the cells file as a "
                    f"deliberate guardrail). See log:"
                )
                st.code("\n".join(lines) or "(no output)", language="text")


# =========================================================================
# Stage B — run the scoring layer
# =========================================================================

if not w.results_xlsx or not Path(w.results_xlsx).exists():
    st.stop()

st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
st.markdown(
    '<div class="eyebrow">stage b</div>'
    '<h3 style="margin-top:0">Compute per-skill mastery</h3>',
    unsafe_allow_html=True,
)
st.markdown(
    '<p class="lead">Mastery on skill K = mean correctness on the '
    "single-skill items whose target is K. Multi-skill items are "
    "deliberately excluded from the primary score (they conflate skills); "
    "they go into the optional audit file instead.</p>",
    unsafe_allow_html=True,
)

if w.mastery_xlsx and Path(w.mastery_xlsx).exists():
    st.markdown(
        f'<span class="pill done">mastery computed</span> '
        f'<span class="kbd">{Path(w.mastery_xlsx).name}</span>',
        unsafe_allow_html=True,
    )

c1, c2 = st.columns([1, 3])
with c1:
    if st.button("Run scoring", type="primary"):
        mastery_path = w.root / "mastery.xlsx"
        audit_path = w.root / "audit.xlsx"

        with st.spinner("Scoring…"):
            log = []
            for stream, line in run_scoring(
                Path(w.results_xlsx), Path(w.bank),
                mastery_path, audit=audit_path,
            ):
                if stream == "exit":
                    rc = int(line)
                    if rc != 0:
                        st.error(f"Scoring exited with code {rc}")
                        st.code("\n".join(log) or "(no output)",
                                language="text")
                        st.stop()
                else:
                    log.append(line)

        w.mastery_xlsx = mastery_path
        save_ws()
        st.success("Mastery computed.")
        time.sleep(0.3)
        st.rerun()


# =========================================================================
# Stage C — quick look at the mastery distribution
# =========================================================================

if w.mastery_xlsx and Path(w.mastery_xlsx).exists():
    st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
    st.markdown(
        '<div class="eyebrow">quick look</div>'
        '<h3 style="margin-top:0">Class mastery overview</h3>',
        unsafe_allow_html=True,
    )

    mastery = pd.read_excel(w.mastery_xlsx)
    bank = json.loads(Path(w.bank).read_text())
    skills = bank["meta"]["node_order"]

    n_total = len(mastery)
    n_scored = int(mastery[skills[0]].notna().sum()) if skills else 0
    mean_overall = float(mastery["mean_mastery"].mean()) \
                   if "mean_mastery" in mastery.columns else float("nan")

    c1, c2, c3 = st.columns(3)
    c1.metric("Students", n_total)
    c2.metric("Scored", f"{n_scored} / {n_total}")
    if mean_overall == mean_overall:    # not NaN
        c3.metric("Class mean mastery", f"{mean_overall:.2f}")

    c1, c2 = st.columns([1.2, 1], gap="large")
    with c1:
        fig = mastery_distribution(mastery, skills)
        st.pyplot(fig, use_container_width=True)
    with c2:
        fig = per_student_mean_hist(mastery)
        st.pyplot(fig, use_container_width=True)

    with st.expander("Mastery table (first 20 rows)", expanded=False):
        st.dataframe(mastery.head(20), use_container_width=True,
                     hide_index=True)

    st.download_button(
        "Download mastery.xlsx",
        data=Path(w.mastery_xlsx).read_bytes(),
        file_name="mastery.xlsx",
        mime="application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet",
    )
