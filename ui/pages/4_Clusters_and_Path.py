"""Page 4 — Clusters & Path.

Runs hierarchical clustering on the mastery vectors, then walks the
prerequisite DAG to put each cluster's deficits in remediation order.
The page focuses on showing the teacher *what the cluster structure
looks like* — the centroid heatmap and the size bar are usually the
two charts that drive the pedagogical conversation.
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
from lib.runners import run_clustering, run_remediation_layer
from lib.viz import cluster_centroid_heatmap, cluster_size_bar


st.set_page_config(page_title="Clusters & Path — mehuwo", page_icon="📐",
                   layout="wide")
init_state()
w = require_ws()


header(
    "Clusters & Path",
    eyebrow="step 4 of 5",
    description=(
        "Group students by mastery profile, label each cluster by where "
        "the deficits sit in the skill graph, and sequence each cluster's "
        "deficits into a remediation path that respects prerequisites."
    ),
)


if not w.mastery_xlsx or not Path(w.mastery_xlsx).exists():
    st.warning("Compute mastery first on the **Score & Mastery** page.")
    st.stop()


# =========================================================================
# Stage A — clustering
# =========================================================================

st.markdown(
    '<div class="eyebrow">stage a</div>'
    '<h3 style="margin-top:0">Cluster the class</h3>',
    unsafe_allow_html=True,
)
st.markdown(
    '<p class="lead">Hierarchical clustering (Ward linkage, Euclidean) '
    "on the per-skill mastery vectors. Clusters are renumbered so "
    "cluster 0 is weakest. Each cluster gets a label inferred from "
    "where its deficits sit in the DAG: foundation-gap, middle-gap, "
    "leaf-gap, broad-gap, or all-mastered.</p>",
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    k = st.number_input("Number of clusters (k)",
                        min_value=2, max_value=8, value=3)
with c2:
    debug = st.checkbox(
        "Write debug plots", value=True,
        help="Dendrogram, silhouette-by-k, centroid heatmap as PNGs.",
    )

with c3:
    if st.button("Run clustering", type="primary"):
        mc_path = w.root / "mastery_clustered.xlsx"
        summary_path = w.root / "cluster_summary.xlsx"
        debug_dir = (w.root / "debug") if debug else None

        with st.spinner("Clustering…"):
            log = []
            for stream, line in run_clustering(
                Path(w.mastery_xlsx), Path(w.bank),
                mc_path, summary_path,
                k=int(k), debug_dir=debug_dir,
            ):
                if stream == "exit":
                    rc = int(line)
                    if rc != 0:
                        st.error(f"Clustering exited with code {rc}")
                        st.code("\n".join(log) or "(no output)",
                                language="text")
                        st.stop()
                else:
                    log.append(line)

        w.mastery_clustered_xlsx = mc_path
        w.cluster_summary_xlsx = summary_path
        save_ws()
        st.success("Clustering complete.")
        st.code("\n".join(log[-20:]), language="text")
        time.sleep(0.3)
        st.rerun()


# =========================================================================
# Show clusters
# =========================================================================

if not (w.cluster_summary_xlsx and Path(w.cluster_summary_xlsx).exists()):
    st.stop()

summary = pd.read_excel(w.cluster_summary_xlsx)
bank = json.loads(Path(w.bank).read_text())
skills = bank["meta"]["node_order"]

st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
st.markdown(
    '<div class="eyebrow">cluster overview</div>'
    '<h3 style="margin-top:0">What the class looks like</h3>',
    unsafe_allow_html=True,
)

# Per-cluster cards
for _, row in summary.iterrows():
    label_class = {
        "all-mastered": "done",
        "foundation-gap": "warn",
        "middle-gap": "warn",
        "leaf-gap": "warn",
        "broad-gap": "error",
    }.get(row["label"], "todo")

    # `weakest_skills` may be missing (NaN) for the `all-mastered` cluster
    # and pandas returns float NaN, not empty string. Coerce explicitly.
    weak_raw = row.get("weakest_skills", "")
    weak = str(weak_raw).strip() if pd.notna(weak_raw) else ""

    st.markdown(
        f'<div class="card">'
        f'<div style="display:flex;align-items:baseline;'
        f'justify-content:space-between;margin-bottom:0.3rem">'
        f'<h4 style="margin:0">Cluster {int(row["cluster"])}</h4>'
        f'<span class="pill {label_class}">{row["label"]}</span>'
        f'</div>'
        f'<div class="muted" style="font-size:0.88rem">'
        f'<b>{int(row["size"])}</b> students · '
        f'mean mastery <b>{row["mean_mastery"]:.2f}</b>'
        f'{(" · weakest in " + weak) if weak else ""}'
        f'</div></div>',
        unsafe_allow_html=True,
    )

# Charts
c1, c2 = st.columns([1.6, 1], gap="large")
with c1:
    fig = cluster_centroid_heatmap(summary, skills)
    st.pyplot(fig, use_container_width=True)
with c2:
    fig = cluster_size_bar(summary)
    st.pyplot(fig, use_container_width=True)

# Optional debug plots from the clustering layer
if (w.root / "debug").exists():
    with st.expander("Clustering validation plots", expanded=False):
        st.caption(
            "Dendrogram and silhouette-by-k from the clustering layer. "
            "If the silhouette at the chosen k is below 0.15, the "
            "clusters aren't well-separated — try a different k."
        )
        c1, c2 = st.columns(2)
        for col, name in zip([c1, c2], ["dendrogram.png", "silhouette.png"]):
            p = w.root / "debug" / name
            if p.exists():
                col.image(str(p), caption=name, use_container_width=True)
        hm = w.root / "debug" / "heatmap.png"
        if hm.exists():
            st.image(str(hm), caption="heatmap.png (mirror of the chart above)",
                     use_container_width=True)


# =========================================================================
# Stage B — remediation path
# =========================================================================

st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
st.markdown(
    '<div class="eyebrow">stage b</div>'
    '<h3 style="margin-top:0">Sequence the remediation</h3>',
    unsafe_allow_html=True,
)
st.markdown(
    '<p class="lead">Each cluster\'s deficit skills are put in '
    "topological order through the prerequisite DAG. Foundations come "
    "before leaves; ties are broken by DAG depth so foundational "
    "deficits get addressed before parallel deficits that don't "
    "strictly require them.</p>",
    unsafe_allow_html=True,
)

c1, c2 = st.columns([1, 2])
with c1:
    partial_threshold = st.slider(
        "Mastery threshold", 0.0, 1.0, 0.5, 0.05,
        help="Skills below this count as deficits and enter the path.",
    )
with c2:
    if st.button("Build remediation paths", type="primary"):
        path_out = w.root / "cluster_paths.xlsx"
        with st.spinner("Walking the skill graph…"):
            log = []
            for stream, line in run_remediation_layer(
                Path(w.mastery_clustered_xlsx),
                Path(w.bank),
                path_out,
                partial_threshold=float(partial_threshold),
            ):
                if stream == "exit":
                    rc = int(line)
                    if rc != 0:
                        st.error(f"Remediation layer exited with code {rc}")
                        st.code("\n".join(log), language="text")
                        st.stop()
                else:
                    log.append(line)
        w.cluster_paths_xlsx = path_out
        save_ws()
        st.success("Remediation paths written.")
        time.sleep(0.3)
        st.rerun()


if w.cluster_paths_xlsx and Path(w.cluster_paths_xlsx).exists():
    paths = pd.read_excel(w.cluster_paths_xlsx)
    st.markdown(
        f'<span class="pill done">paths built</span> '
        f'<span class="kbd">{Path(w.cluster_paths_xlsx).name}</span> · '
        f'{len(paths)} steps total',
        unsafe_allow_html=True,
    )

    # Show path per cluster
    if "cluster" in paths.columns:
        for c, group in paths.groupby("cluster"):
            st.markdown(f"**Cluster {int(c)} path**")
            steps = []
            for _, row in group.iterrows():
                # NaN-safe reads (pandas hands back float NaN for missing cells)
                band_raw = row.get("band") if pd.notna(row.get("band")) \
                           else row.get("type")
                band = str(band_raw).strip() if pd.notna(band_raw) else ""
                skill_raw = row.get("skill", "")
                skill = str(skill_raw) if pd.notna(skill_raw) else ""
                step_raw = row.get("step", 0)
                step_n = int(step_raw) if pd.notna(step_raw) else 0

                band_pill = {
                    "foundation": '<span class="pill warn">foundation</span>',
                    "middle":     '<span class="pill warn">middle</span>',
                    "leaf":       '<span class="pill">leaf</span>',
                }.get(band, f'<span class="pill">{band}</span>' if band
                                else '<span class="pill"></span>')
                steps.append(
                    f"<div style='margin:0.25rem 0;font-size:0.92rem'>"
                    f"<b>{step_n}.</b> "
                    f"<span style='font-family:JetBrains Mono;"
                    f"font-size:0.88rem'>{skill}</span> "
                    f"{band_pill}</div>"
                )
            st.markdown("\n".join(steps), unsafe_allow_html=True)
            st.markdown("")

    st.download_button(
        "Download cluster_paths.xlsx",
        data=Path(w.cluster_paths_xlsx).read_bytes(),
        file_name="cluster_paths.xlsx",
        mime="application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet",
    )
