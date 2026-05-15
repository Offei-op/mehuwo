"""mehuwo — Streamlit home.

Two jobs:
  1. Workspace lifecycle: create new, load existing, or open the demo.
  2. Show pipeline progress for the active workspace so the teacher
     knows what's done and what to do next.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Make `lib` importable when running `streamlit run ui/app.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.state import (
    init_state, ws, header, STEPS, step_status,
    create_workspace, load_workspace, list_workspaces, make_demo_workspace,
    BUNDLED,
)
from lib.runners import ollama_reachable


st.set_page_config(
    page_title="mehuwo — adaptive diagnostics",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_state()


# --- Sidebar --------------------------------------------------------------

with st.sidebar:
    st.markdown(
        "<div style='padding:0.3rem 0 0.8rem 0;"
        "font-family:Plus Jakarta Sans;font-size:1.35rem;"
        "font-weight:700;letter-spacing:-0.02em;color:#2D4A2B;'>"
        "mehuwo</div>"
        "<div class='muted' style='font-size:0.78rem;line-height:1.45;"
        "margin-bottom:1rem'>Adaptive math diagnostics<br>"
        "for Ghanaian classrooms.</div>",
        unsafe_allow_html=True,
    )

    st.markdown("## Workspace")
    w = ws()
    if w is None:
        st.markdown(
            '<span class="pill warn">none selected</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="font-size:0.9rem;font-weight:600;'
            f'color:#2D4A2B;margin-bottom:0.1rem;">{w.name}</div>'
            f'<div class="muted" style="font-size:0.75rem">'
            f'{w.root}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("## Local LLM")
    ok, detail = ollama_reachable(st.session_state["ollama_host"])
    if ok:
        st.markdown(
            f'<span class="pill done">Ollama reachable</span>'
            f'<div class="muted" style="font-size:0.75rem;margin-top:0.4rem">'
            f'{detail}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<span class="pill error">Ollama not reachable</span>'
            f'<div class="muted" style="font-size:0.72rem;'
            f'margin-top:0.4rem">{detail[:120]}</div>',
            unsafe_allow_html=True,
        )
        st.caption("LLM steps will fail. Run `ollama serve` and pull the "
                   "model (default `gemma4:e4b`).")


# --- Header ---------------------------------------------------------------

header(
    "Diagnose, cluster, remediate.",
    eyebrow="mehuwo · home",
    description=(
        "A pipeline for turning a paper math test into a per-skill mastery "
        "profile, grouping students into pedagogically meaningful clusters, "
        "and producing printable remediation packs that target each cluster's "
        "specific gaps."
    ),
)


# --- Workspace lifecycle --------------------------------------------------

w = ws()

if w is None:
    st.markdown(
        '<div class="eyebrow">Get started</div>'
        '<h3 style="margin-top:0">Choose a workspace</h3>',
        unsafe_allow_html=True,
    )

    col_a, col_b, col_c = st.columns([1, 1, 1], gap="large")

    # --- New workspace -----------------------------------------------
    with col_a:
        st.markdown(
            '<div class="card"><h4>Start a new diagnostic</h4>'
            '<div class="muted" style="font-size:0.88rem;'
            'margin-bottom:0.8rem">For a real class. Pick a short name '
            '(e.g. <span class="kbd">jhs2a-2026-term1</span>); we\'ll keep '
            'every file for this run inside one folder.</div></div>',
            unsafe_allow_html=True,
        )
        new_name = st.text_input(
            "Workspace name", placeholder="jhs2a-2026-term1",
            label_visibility="collapsed",
        )
        if st.button("Create workspace", type="primary",
                     use_container_width=True, disabled=not new_name.strip()):
            create_workspace(new_name.strip())
            st.rerun()

    # --- Existing workspaces -----------------------------------------
    with col_b:
        st.markdown(
            '<div class="card"><h4>Open an existing one</h4>'
            '<div class="muted" style="font-size:0.88rem;'
            'margin-bottom:0.8rem">Continue a diagnostic you started '
            'earlier. Workspaces remember every output produced so far.'
            '</div></div>',
            unsafe_allow_html=True,
        )
        existing = list_workspaces()
        if not existing:
            st.caption("No saved workspaces yet.")
        else:
            choice = st.selectbox(
                "Existing", options=existing,
                format_func=lambda p: p.name,
                label_visibility="collapsed",
            )
            if st.button("Open", use_container_width=True):
                load_workspace(choice)
                st.rerun()

    # --- Demo --------------------------------------------------------
    with col_c:
        st.markdown(
            '<div class="card"><h4>Just looking around?</h4>'
            '<div class="muted" style="font-size:0.88rem;'
            'margin-bottom:0.8rem">Open the demo workspace. It points '
            'at the bundled Fractions item bank, quiz, and roster sheet '
            "so you can explore every screen without running the LLM."
            "</div></div>",
            unsafe_allow_html=True,
        )
        missing = [k for k, p in BUNDLED.items() if not p.exists()]
        if missing:
            st.caption(f"Some bundled files missing: {', '.join(missing)}")
        if st.button("Open demo workspace", use_container_width=True,
                     disabled=bool(missing)):
            make_demo_workspace()
            st.rerun()

    st.markdown(
        '<hr class="section-rule">'
        '<div class="eyebrow">About</div>'
        '<h3 style="margin-top:0">How the pipeline runs</h3>',
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns([1.1, 1], gap="large")
    with c1:
        st.markdown(
            """
            Each page in the sidebar is one stage of a nine-step pipeline.
            You work top to bottom — the output of each stage feeds the next.

            1.  **Topic & Items** — define the unit you want to assess
                (the bundled spec covers a 10-skill Fractions unit), then
                have the local LLM author a multiple-choice item bank.
                Every item is arithmetically verified before it's allowed
                into the bank.
            2.  **Materials** — print the quiz, the answer key, and the
                class roster sheet (with ArUco fiducials so the OCR step
                can rectify it from a photo).
            3.  **Score** — upload the per-item correctness file produced
                by marking, get a per-skill mastery profile per student.
            4.  **Clusters & Path** — group students by mastery profile,
                walk the prerequisite graph to put each cluster's
                deficits in remediation order.
            5.  **Author & Print** — the LLM writes a diagnostic narrative
                for each cluster and a teaching pack for each (cluster,
                skill) step. Print everything as PDFs.
            """,
        )
    with c2:
        if BUNDLED["skill_graph"].exists():
            st.image(
                str(BUNDLED["skill_graph"]),
                caption="The bundled Fractions unit — 10 skills and their "
                        "prerequisite edges.",
                use_container_width=True,
            )

    st.stop()


# --- With a workspace selected -------------------------------------------

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    st.markdown(
        f'<div class="eyebrow">active workspace</div>'
        f'<h3 style="margin-top:0;color:#2D4A2B">{w.name}</h3>'
        f'<div class="muted" style="font-size:0.85rem;margin-top:-0.3rem">'
        f'created {w.created_at} · '
        f'<span class="kbd">{w.root}</span></div>',
        unsafe_allow_html=True,
    )
with c2:
    st.metric("Steps complete",
              f"{sum(1 for _, _, d in step_status(w) if d)} / {len(STEPS)}")
with c3:
    if st.button("Switch workspace", use_container_width=True):
        st.session_state["workspace"] = None
        st.rerun()

st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)

# --- Pipeline progress ---------------------------------------------------

st.markdown(
    '<div class="eyebrow">progress</div>'
    '<h3 style="margin-top:0">Where you are in the pipeline</h3>',
    unsafe_allow_html=True,
)

cols = st.columns(len(STEPS))
for col, (attr, label, done) in zip(cols, step_status(w)):
    with col:
        pill = '<span class="pill done">done</span>' if done \
               else '<span class="pill todo">todo</span>'
        st.markdown(
            f'<div style="text-align:center;line-height:1.4">'
            f'{pill}<div style="font-size:0.78rem;color:#5C5346;'
            f'margin-top:0.4rem">{label}</div></div>',
            unsafe_allow_html=True,
        )

st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)

# --- Quick links ---------------------------------------------------------

st.markdown(
    '<div class="eyebrow">jump to</div>'
    '<h3 style="margin-top:0">Next steps</h3>',
    unsafe_allow_html=True,
)

# Find the first incomplete step and suggest it
status = step_status(w)
next_step = next(((a, l) for a, l, d in status if not d), None)

if next_step is None:
    st.success("Every step is complete. Head to **Author & Print** to "
               "download the PDFs, or start a new workspace for the next class.")
else:
    attr, label = next_step
    page_map = {
        "topic_spec": "1_Topic_and_Items",
        "bank": "1_Topic_and_Items",
        "quiz_pdf": "2_Materials",
        "results_xlsx": "3_Score_and_Mastery",
        "mastery_xlsx": "3_Score_and_Mastery",
        "mastery_clustered_xlsx": "4_Clusters_and_Path",
        "cluster_paths_xlsx": "4_Clusters_and_Path",
        "remediation_pack_json": "5_Author_and_Print",
        "remediation_pack_pdf": "5_Author_and_Print",
    }
    st.info(f"Next up: **{label}**. Use the sidebar to open "
            f"`{page_map.get(attr, '1_Topic_and_Items').replace('_', ' ')}`.")

# --- Workspace files ------------------------------------------------------

with st.expander("Files in this workspace", expanded=False):
    rows = []
    for attr, label, done in status:
        p = getattr(w, attr)
        if p:
            try:
                exists = Path(p).exists()
                size = Path(p).stat().st_size if exists else 0
                rows.append({
                    "Step": label,
                    "File": str(Path(p).name),
                    "Size (KB)": f"{size/1024:.1f}" if exists else "—",
                    "Status": "✓" if exists else "missing",
                })
            except Exception:
                pass
    if rows:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     hide_index=True)
    else:
        st.caption("No artefacts produced yet.")
