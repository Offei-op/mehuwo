"""Upload, recognize and review correctness, then diagnose skill mastery."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.state import init_state, require_ws, save_ws, header, invalidate_after
from lib.runners import run_scoring
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
        "Import results or recognize and review a roster scan, then estimate "
        "skill mastery and identify where more evidence is needed."
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
    order = st.radio("Response column order", ["Original bank order", "Printed form order"])
    manifest_upload = st.file_uploader("Form manifest (for printed order)", type=["json"])
    if st.button("Import results", disabled=up is None):
        import uuid
        from assessment.contracts import read_results
        try:
            bank_data = json.loads(Path(w.bank).read_text(encoding="utf-8"))
            form = None
            if order == "Printed form order":
                if manifest_upload:
                    form = json.loads(manifest_upload.getvalue())
                elif w.quiz_manifest:
                    form = json.loads(Path(w.quiz_manifest).read_text(encoding="utf-8"))
                else:
                    raise ValueError("Upload the matching form manifest.")
            incoming = read_results(up, bank_data, form)
            folder = w.root / "imports" / str(uuid.uuid4())
            folder.mkdir(parents=True)
            (folder / "original.xlsx").write_bytes(up.getvalue())
            from assessment.contracts import digest
            with pd.ExcelWriter(folder / "results.xlsx") as writer:
                incoming.to_excel(writer, sheet_name="results", index=False)
                pd.DataFrame([{"bank_version": digest(bank_data), "column_order": "canonical"}]).to_excel(
                    writer, sheet_name="assessment_meta", index=False)
            from assessment.contracts import digest, now
            (folder / "provenance.json").write_text(json.dumps({
                "bank_version": digest(bank_data), "imported_at": now(), "source_order": order,
                "form": form,
            }, indent=2), encoding="utf-8")
            invalidate_after(w, "results")
            w.results_xlsx = folder / "results.xlsx"
            save_ws()
            st.rerun()
        except (ValueError, KeyError, TypeError) as exc:
            st.error(str(exc))

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
        invalidate_after(w, "results")
        w.results_xlsx = dest
        save_ws()
        st.success(f"Generated {len(df)} synthetic students.")
        time.sleep(0.4)
        st.rerun()

with tab_ocr:
    from lib.scan import render_scan_review
    render_scan_review(w)


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
    '<p class="lead">Estimate skill mastery from question responses. '
    "The probabilistic model combines single- and multi-skill evidence, "
    "allows for guesses and mistakes, and flags uncertain diagnoses.</p>",
    unsafe_allow_html=True,
)

if w.mastery_xlsx and Path(w.mastery_xlsx).exists():
    st.markdown(
        f'<span class="pill done">mastery computed</span> '
        f'<span class="kbd">{Path(w.mastery_xlsx).name}</span>',
        unsafe_allow_html=True,
    )

method = st.selectbox("Diagnostic method", ["dina", "simple"],
                      format_func=lambda m: "Probabilistic diagnosis (DINA)" if m == "dina"
                      else "Single-skill averages (comparison baseline)")
slip, guess, prior, confidence = 0.1, 0.25, 0.5, 0.8
if method == "dina":
    st.info("Probabilities depend on assumed item parameters and the skill mapping. "
            "They have not been calibrated against classroom data. Uncertain means "
            "more evidence is needed, not partial mastery.")
    with st.expander("Model assumptions"):
        slip = st.slider("Mistake probability when required skills are mastered", 0.01, 0.49, 0.10, 0.01)
        guess = st.slider("Correct-answer probability without all required skills", 0.01, 0.49, 0.25, 0.01)
        prior = st.slider("Prior mastery probability", 0.05, 0.95, 0.50, 0.05)
        confidence = st.slider("Probability required for likely mastery", 0.60, 0.95, 0.80, 0.05)
        st.caption("Per-item diagnostic_parameters in the bank override slip and guess. "
                   "Blank response cells are missing evidence; use 0 for a wrong answer.")

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
                method=method, slip=slip, guess=guess, prior=prior, confidence=confidence,
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
        # Downstream artifacts describe the previous diagnosis until rebuilt.
        for attr in ("mastery_clustered_xlsx", "cluster_summary_xlsx", "cluster_paths_xlsx",
                     "cluster_summary_narratives_xlsx", "remediation_pack_json",
                     "cluster_report_pdf", "remediation_pack_pdf"):
            setattr(w, attr, None)
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

    if "diagnostic_method" in mastery and mastery["diagnostic_method"].eq("dina").any():
        st.caption("Skill values are posterior mastery probabilities, not percent correct. "
                   "Class averages do not establish mastery for every student.")
        diagnostic = pd.read_excel(w.mastery_xlsx, sheet_name="diagnostics")
        counts = diagnostic["status"].value_counts()
        a, b, c = st.columns(3)
        a.metric("Likely mastered skills", int(counts.get("likely_mastered", 0)))
        b.metric("Uncertain skills", int(counts.get("uncertain", 0)))
        c.metric("Skills without evidence", int(counts.get("insufficient_evidence", 0)))
        selected = st.selectbox("Inspect student", mastery["student_idx"].tolist())
        st.dataframe(diagnostic[diagnostic["student_idx"] == selected], hide_index=True)
        with st.expander("Recommended follow-up checks", expanded=True):
            follow = pd.read_excel(w.mastery_xlsx, sheet_name="follow_up")
            st.dataframe(follow[follow["student_idx"] == selected], hide_index=True)
            st.caption("Recommendations rank missing bank responses by expected information gain. "
                       "Review omissions before reusing questions. When the bank is exhausted, "
                       "prepare new questions for the listed skills.")

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
