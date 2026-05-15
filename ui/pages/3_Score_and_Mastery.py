"""Page 3 — Score & Mastery.

Honest about the missing OCR step: provides three ways to get a
results.xlsx:
  1. Upload a real one (produced manually or by an external OCR step)
  2. Generate synthetic results from the bank for demo / testing
  3. (Placeholder) — upload scanned roster sheet. This will be enabled
     once the recognition pipeline lands.

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
        "Turn per-item correctness into per-skill mastery. Currently you "
        "must supply the results file manually (or generate synthetic "
        "results for testing) — the OCR step that reads filled-in roster "
        "sheets is on the roadmap but not yet built."
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
    st.markdown(
        '<div class="card"><h4>Not yet available</h4>'
        '<div class="muted" style="font-size:0.88rem">Uploading a phone '
        "photo of a filled-in roster sheet and getting per-item "
        "correctness back is the next thing on the roadmap. The roster "
        "sheets <em>already</em> carry ArUco fiducials and the "
        "cells.json file already records every tick-cell position, so "
        "the OCR step has everything it needs — the rectification, "
        "tick-classification, and writeback code just hasn't been built "
        "yet. For now, use one of the other tabs."
        "</div></div>",
        unsafe_allow_html=True,
    )


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
