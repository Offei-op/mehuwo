"""Page 5 — Author & Print.

The two LLM-authoring steps plus the two PDF renderers, in the order
the teacher needs them:

  1. cluster_narrative  -- one paragraph per cluster explaining what
                           the cluster likely understands / misunder-
                           stands / what to watch for during teaching.
  2. cluster_report_pdf -- combine the narratives with the centroid
                           heatmap into a printable diagnostic.
  3. remediation_content -- per (cluster, skill) teaching micro-pack:
                            explanation, worked example, 3 practice
                            items, 2-3 common mistakes.
  4. remediation_to_pdf -- the printable teaching pack.

Both LLM steps support resume (especially important for
remediation_content, which can take an hour for k=3 clusters with
6-step paths each).
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
from lib.runners import (
    run_cluster_narrative, run_remediation_content,
    run_cluster_report_pdf, run_remediation_to_pdf,
    ollama_reachable,
)


st.set_page_config(page_title="Author & Print — mehuwo", page_icon="📐",
                   layout="wide")
init_state()
w = require_ws()


header(
    "Author & Print",
    eyebrow="step 5 of 5",
    description=(
        "Have the LLM write the diagnostic narratives and the teaching "
        "packs, then print everything as PDFs the teacher can hand around. "
        "These are the two longest-running steps in the pipeline."
    ),
)


if not w.cluster_paths_xlsx or not Path(w.cluster_paths_xlsx).exists():
    st.warning("Build the cluster paths first on the "
               "**Clusters & Path** page.")
    st.stop()


# Quick LLM check
ok, detail = ollama_reachable(st.session_state["ollama_host"])
if not ok:
    st.error(f"Ollama not reachable. Start the daemon "
             f"before running these steps. Detail: {detail}")


# Helpers ------------------------------------------------------------------

def _render_skill_pack(step: dict) -> None:
    """Pretty-print one (cluster, skill) teaching pack."""
    skill = step.get("skill", "—")
    st.markdown(
        f'<div class="card">'
        f'<h4 style="margin:0 0 0.4rem 0">Skill: '
        f'<span style="font-family:JetBrains Mono;font-size:0.95rem">'
        f'{skill}</span></h4>',
        unsafe_allow_html=True,
    )

    if step.get("explanation"):
        st.markdown("**Explanation**")
        st.markdown(f'<div class="muted" style="margin-bottom:0.7rem">'
                    f'{step["explanation"]}</div>',
                    unsafe_allow_html=True)

    we = step.get("worked_example")
    if we:
        st.markdown("**Worked example**")
        st.markdown(f"_Problem._ {we.get('problem', '')}")
        for i, s in enumerate(we.get("solution_steps", []), 1):
            st.markdown(f"&nbsp;&nbsp;{i}. {s}", unsafe_allow_html=True)
        if we.get("final_answer"):
            st.markdown(
                f"_Answer._ <b>{we['final_answer']}</b>",
                unsafe_allow_html=True,
            )

    practice = step.get("practice_items", [])
    if practice:
        st.markdown("**Practice items**")
        for i, p in enumerate(practice, 1):
            st.markdown(f"&nbsp;&nbsp;**{i}.** {p.get('stem', '')}",
                        unsafe_allow_html=True)
            opts = p.get("options", {})
            correct = p.get("correct_option", "")
            iter_opts = opts.items() if isinstance(opts, dict) \
                        else zip("ABCD", opts)
            for k, v in iter_opts:
                marker = "✓" if k == correct else "·"
                st.markdown(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;{marker} **{k}.** {v}",
                    unsafe_allow_html=True,
                )
            if p.get("hint"):
                st.caption(f"      hint: {p['hint']}")

    mistakes = step.get("common_mistakes", [])
    if mistakes:
        st.markdown("**Common mistakes**")
        for m in mistakes:
            st.markdown(
                f"&nbsp;&nbsp;• **{m.get('name', '')}** — "
                f"{m.get('description', '')} "
                f"<span class='muted'>"
                f"<i>Fix:</i> {m.get('correction', '')}</span>",
                unsafe_allow_html=True,
            )

    st.markdown("</div>", unsafe_allow_html=True)


def _run_with_log(gen, busy_message: str,
                  success_message: str) -> bool:
    """Run a subprocess generator with a live log + spinner. Returns
    True if the subprocess exited 0."""
    log = []
    log_widget = st.empty()
    progress_widget = st.empty()
    with st.spinner(busy_message):
        rc = None
        
        t0 = time.time()
        for stream, line in gen:
            if stream == "exit":
                rc = int(line)
                break
            log.append(line)
            log_widget.code("\n".join(log[-30:]), language="text")
            progress_widget.markdown(
                f"<div class='muted' style='font-size:0.8rem'>"
                f"running… {time.time()-t0:.0f}s · {len(log)} lines"
                f"</div>", unsafe_allow_html=True,
            )
    progress_widget.empty()
    if rc == 0:
        st.success(success_message)
        return True
    st.error(f"Exited with code {rc}.")
    return False


tab_narr, tab_content, tab_pdfs = st.tabs([
    "1 · Cluster narratives",
    "2 · Remediation content",
    "3 · Print PDFs",
])


# =========================================================================
# TAB 1: cluster narratives
# =========================================================================

with tab_narr:
    st.markdown(
        '<div class="eyebrow">cluster narratives</div>'
        '<h3 style="margin-top:0">One diagnostic paragraph per cluster</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="lead">For each cluster the LLM writes three short '
        "paragraphs: what students in this cluster likely understand, "
        "what they probably misunderstand, and what a teacher should "
        "watch for during remediation. Grounded in the centroid data — "
        "no generic advice.</p>",
        unsafe_allow_html=True,
    )

    if w.cluster_summary_narratives_xlsx and \
            Path(w.cluster_summary_narratives_xlsx).exists():
        st.markdown(
            f'<span class="pill done">narratives ready</span> '
            f'<span class="kbd">'
            f'{Path(w.cluster_summary_narratives_xlsx).name}</span>',
            unsafe_allow_html=True,
        )

    c1, c2 = st.columns(2)
    with c1:
        model = st.text_input(
            "Model", value=st.session_state["ollama_model"],
            key="narr_model",
        )
    with c2:
        also_students = st.checkbox(
            "Also write per-student narratives",
            help="Useful for parent-teacher reports. Adds 30–60s per "
                 "student.",
        )

    only_clusters = None
    student_output_path = None
    if also_students:
        only_clusters_in = st.text_input(
            "Limit to clusters (comma-separated, optional)",
            placeholder="e.g. 0,1",
        )
        only_clusters = only_clusters_in.strip() or None
        student_output_path = w.root / "student_narratives.xlsx"

    if st.button("Write cluster narratives", type="primary",
                 disabled=not ok):
        out = w.root / "cluster_summary_with_narratives.xlsx"
        success = _run_with_log(
            run_cluster_narrative(
                cluster_summary=Path(w.cluster_summary_xlsx),
                bank=Path(w.bank),
                output=out,
                model=model.strip() or None,
                host=st.session_state["ollama_host"],
                mastery_clustered=Path(w.mastery_clustered_xlsx)
                                  if also_students else None,
                student_output=student_output_path,
                only_clusters=only_clusters,
            ),
            "Writing narratives…",
            "Narratives written.",
        )
        if success and out.exists():
            w.cluster_summary_narratives_xlsx = out
            save_ws()
            time.sleep(0.4)
            st.rerun()

    # Preview ----------------------------------------------------------
    if w.cluster_summary_narratives_xlsx and \
            Path(w.cluster_summary_narratives_xlsx).exists():
        df = pd.read_excel(w.cluster_summary_narratives_xlsx)
        st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
        st.markdown(
            '<div class="eyebrow">preview</div>'
            '<h3 style="margin-top:0">What the LLM wrote</h3>',
            unsafe_allow_html=True,
        )
        for _, row in df.iterrows():
            label_raw = row.get("label", "")
            label = str(label_raw) if pd.notna(label_raw) else ""
            st.markdown(
                f'<div class="card">'
                f'<h4 style="margin:0 0 0.4rem 0">Cluster '
                f'{int(row["cluster"])} '
                f'<span class="pill todo">{label}</span>'
                f'</h4>'
                f'<div class="muted" style="font-size:0.78rem;'
                f'margin-bottom:0.6rem">n={int(row["size"])} · '
                f'mean mastery {row["mean_mastery"]:.2f}</div>',
                unsafe_allow_html=True,
            )

            for field, title in [
                ("likely_understands", "Likely understands"),
                ("likely_misunderstands", "Likely misunderstands"),
                ("teaching_watchpoints", "Teaching watchpoints"),
            ]:
                if field in row and pd.notna(row[field]):
                    st.markdown(
                        f'<div style="margin-bottom:0.5rem"><b>{title}.</b> '
                        f'{row[field]}</div>',
                        unsafe_allow_html=True,
                    )
            st.markdown("</div>", unsafe_allow_html=True)


# =========================================================================
# TAB 2: remediation content
# =========================================================================

with tab_content:
    st.markdown(
        '<div class="eyebrow">remediation content</div>'
        '<h3 style="margin-top:0">Teaching pack per cluster and skill</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="lead">For every step in each cluster\'s remediation '
        "path, the LLM authors a teaching micro-pack: a 100-word "
        "explanation, a worked example with numbered solution steps, "
        "three practice MCQs with teacher hints, and 2–3 common "
        "mistakes with corrections. Checkpointed — pass <b>resume</b> "
        "to pick up after a crash.</p>",
        unsafe_allow_html=True,
    )

    if w.remediation_pack_json and Path(w.remediation_pack_json).exists():
        try:
            pack = json.loads(Path(w.remediation_pack_json).read_text())
            n_clusters = len(pack.get("clusters", []))
            n_steps = sum(len(c.get("steps", []))
                          for c in pack.get("clusters", []))
            st.markdown(
                f'<span class="pill done">pack ready</span> '
                f'<span class="kbd">'
                f'{Path(w.remediation_pack_json).name}</span> · '
                f'{n_clusters} cluster(s), {n_steps} skill step(s)',
                unsafe_allow_html=True,
            )
        except Exception:
            pass

    c1, c2 = st.columns(2)
    with c1:
        model = st.text_input(
            "Model", value=st.session_state["ollama_model"],
            key="content_model",
        )
    with c2:
        resume = st.checkbox(
            "Resume from existing pack", value=True,
            help="Skip steps already present in the output JSON. "
                 "Always safe to leave on.",
        )

    st.warning(
        "This is the longest step in the pipeline. Expect 30–90 minutes "
        "for the default fractions setup with k=3 clusters."
    )

    if st.button("Author remediation content", type="primary",
                 disabled=not ok):
        out = w.root / "remediation_pack.json"
        success = _run_with_log(
            run_remediation_content(
                cluster_paths=Path(w.cluster_paths_xlsx),
                cluster_summary=Path(w.cluster_summary_xlsx),
                mastery_clustered=Path(w.mastery_clustered_xlsx),
                bank=Path(w.bank),
                output=out,
                resume=resume,
                model=model.strip() or None,
                host=st.session_state["ollama_host"],
            ),
            "Writing remediation packs…",
            "Remediation content authored.",
        )
        if success and out.exists():
            w.remediation_pack_json = out
            save_ws()
            time.sleep(0.4)
            st.rerun()

    # Preview ----------------------------------------------------------
    if w.remediation_pack_json and Path(w.remediation_pack_json).exists():
        try:
            pack = json.loads(Path(w.remediation_pack_json).read_text())
        except Exception:
            pack = None

        if pack and pack.get("clusters"):
            st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
            st.markdown(
                '<div class="eyebrow">preview</div>'
                '<h3 style="margin-top:0">Sample teaching pack</h3>',
                unsafe_allow_html=True,
            )
            cluster_idx = st.selectbox(
                "Cluster",
                options=range(len(pack["clusters"])),
                format_func=lambda i: f"Cluster {i}",
            )
            cluster = pack["clusters"][cluster_idx]
            steps = cluster.get("steps", [])
            if steps:
                step_idx = st.selectbox(
                    "Step",
                    options=range(len(steps)),
                    format_func=lambda i: (
                        f"{i+1}. {steps[i].get('skill', '—')}"),
                )
                _render_skill_pack(steps[step_idx])


# =========================================================================
# TAB 3: PDFs
# =========================================================================

with tab_pdfs:
    st.markdown(
        '<div class="eyebrow">final deliverables</div>'
        '<h3 style="margin-top:0">Print the teacher-facing PDFs</h3>',
        unsafe_allow_html=True,
    )

    # ---- Cluster report PDF -----------------------------------------
    st.markdown("**Cluster diagnostic report**")
    st.caption("Cover page + one section per cluster with the centroid "
               "heatmap and the LLM narrative.")

    if w.cluster_report_pdf and Path(w.cluster_report_pdf).exists():
        st.markdown(
            f'<span class="pill done">ready</span> '
            f'<span class="kbd">{Path(w.cluster_report_pdf).name}</span>',
            unsafe_allow_html=True,
        )
        st.download_button(
            "Download cluster report PDF",
            data=Path(w.cluster_report_pdf).read_bytes(),
            file_name="cluster_report.pdf",
            mime="application/pdf",
        )

    if st.button("Build cluster report",
                 disabled=not (w.cluster_summary_narratives_xlsx
                               and Path(w.cluster_summary_narratives_xlsx).exists())):
        out = w.root / "cluster_report.pdf"
        success = _run_with_log(
            run_cluster_report_pdf(
                Path(w.cluster_summary_narratives_xlsx),
                Path(w.bank),
                out,
            ),
            "Rendering cluster report…",
            "Cluster report built.",
        )
        if success and out.exists():
            w.cluster_report_pdf = out
            save_ws()
            time.sleep(0.3)
            st.rerun()

    if not (w.cluster_summary_narratives_xlsx and
            Path(w.cluster_summary_narratives_xlsx).exists()):
        st.caption("Write cluster narratives first.")

    st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)

    # ---- Remediation pack PDF ---------------------------------------
    st.markdown("**Remediation teaching pack**")
    st.caption("One section per cluster, then one sub-section per skill "
               "step with explanation, worked example, practice items, "
               "and common mistakes.")

    if w.remediation_pack_pdf and Path(w.remediation_pack_pdf).exists():
        st.markdown(
            f'<span class="pill done">ready</span> '
            f'<span class="kbd">{Path(w.remediation_pack_pdf).name}</span>',
            unsafe_allow_html=True,
        )
        st.download_button(
            "Download remediation pack PDF",
            data=Path(w.remediation_pack_pdf).read_bytes(),
            file_name="remediation_pack.pdf",
            mime="application/pdf",
        )

    split = st.checkbox(
        "Split into one PDF per cluster",
        help="Easier to hand each teacher only their cluster's pack.",
    )

    if st.button("Build remediation PDF",
                 disabled=not (w.remediation_pack_json
                               and Path(w.remediation_pack_json).exists())):
        out_dir = w.root / "remediation_pdfs"
        out_combined = w.root / "remediation_pack.pdf"

        success = _run_with_log(
            run_remediation_to_pdf(
                Path(w.remediation_pack_json),
                out_combined,
                split=split,
                output_dir=out_dir,
            ),
            "Rendering teaching packs…",
            "Remediation PDF(s) built.",
        )
        if success:
            if not split and out_combined.exists():
                w.remediation_pack_pdf = out_combined
            elif split and out_dir.exists():
                # Point at the directory; downloads will list individual files
                w.remediation_pack_pdf = out_dir
            save_ws()
            time.sleep(0.3)
            st.rerun()

    if not (w.remediation_pack_json and
            Path(w.remediation_pack_json).exists()):
        st.caption("Author the remediation content first.")

    # If split mode, list per-cluster downloads
    if w.remediation_pack_pdf and Path(w.remediation_pack_pdf).is_dir():
        st.markdown("**Per-cluster PDFs**")
        for p in sorted(Path(w.remediation_pack_pdf).glob("*.pdf")):
            st.download_button(
                f"Download {p.name}",
                data=p.read_bytes(),
                file_name=p.name,
                mime="application/pdf",
                key=f"dl_{p.name}",
            )
