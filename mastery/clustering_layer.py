"""Clustering layer.

Takes mastery.xlsx (from scoring_layer) plus the item bank JSON, and
groups students into k clusters based on their per-skill mastery vectors.
Interprets each cluster by reading the prerequisite DAG: which skills
are weakest, what depth those skills sit at in the graph, and a short
pedagogical label.

Algorithm:
  - Hierarchical clustering, Ward linkage, Euclidean distance, cut at k.
  - Unscored students (all-NaN mastery rows) are excluded from clustering
    and labelled cluster=-1 in the output.
  - Clusters are re-numbered after fitting so cluster 0 has the lowest
    mean mastery, cluster k-1 has the highest. Stable ordering for
    downstream remediation.

Outputs:
  - mastery_clustered.xlsx: original mastery xlsx + a `cluster` column.
  - cluster_summary.xlsx: one row per cluster with size, mean mastery,
    centroid per-skill mastery, top-3 weakest skills, pedagogical label.
  - optional debug_dir/{dendrogram.png, silhouette.png, heatmap.png}
    for validation of the k choice and visual inspection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from sklearn.metrics import silhouette_score


# ---- skill graph helpers --------------------------------------------------

def compute_depths(prereqs: dict, nodes: list[str]) -> dict[str, int]:
    """Depth of each skill in the DAG: 0 for roots, otherwise
    1 + max(depth of any parent). Used for ordering and labelling."""
    depths: dict[str, int] = {}

    def depth(n: str) -> int:
        if n in depths:
            return depths[n]
        parents = prereqs.get(n, [])
        depths[n] = 0 if not parents else 1 + max(depth(p) for p in parents)
        return depths[n]

    for n in nodes:
        depth(n)
    return depths


def cluster_label(
    centroid: pd.Series, depths: dict[str, int], threshold: float = 0.5,
) -> str:
    """Short pedagogical label inferred from where the deficits sit in
    the DAG. Possible values:

      'all-mastered'   no band has mean centroid below `threshold`.
      'foundation-gap' only the foundation band is deficient.
      'middle-gap'     only the middle band is deficient.
      'leaf-gap'       only the leaf band is deficient.
      'broad-gap'      two or more bands are deficient.

    Rule: split skills into depth bands (foundation / middle / leaf) using
    depth thirds of the DAG. Compute the MEAN centroid of each populated
    band. A band is deficient when its mean is below `threshold`. The
    label is determined by which bands are deficient.

    Why band mean and not "fraction of skills in band below threshold":
    the old rule flipped labels when a single skill crossed the threshold
    line (e.g. moving from 0.51 to 0.49). Band mean is continuous in the
    data, so small perturbations don't change the label.

    Why 'broad-gap' instead of returning the shallowest deficient band
    when multiple bands fail: the shallowest-first rule conflated weak
    clusters with cascade clusters. A cluster whose foundation mean is
    0.49 and whose leaf mean is 0.15 needs different remediation than a
    cluster whose foundation is 0.49 and whose leaf is 0.51 -- both used
    to be labelled 'foundation-gap'. 'broad-gap' tells the teacher the
    deficit spans multiple depths; the remediation layer's topo-sort
    handles the sequencing.
    """
    if len(centroid) == 0:
        return "mixed-gap"

    max_depth = max(depths.values()) if depths else 0
    foundation_cutoff = max_depth / 3.0
    leaf_cutoff = 2.0 * max_depth / 3.0

    def band(d: int) -> str:
        if d <= foundation_cutoff:
            return "foundation"
        if d >= leaf_cutoff:
            return "leaf"
        return "middle"

    sums: dict[str, float] = {"foundation": 0.0, "middle": 0.0, "leaf": 0.0}
    counts: dict[str, int] = {"foundation": 0, "middle": 0, "leaf": 0}
    for s in centroid.index:
        b = band(depths[s])
        sums[b] += float(centroid[s])
        counts[b] += 1

    means = {b: sums[b] / counts[b] for b in sums if counts[b] > 0}
    deficient = [b for b in ("foundation", "middle", "leaf")
                 if b in means and means[b] < threshold]

    if not deficient:
        return "all-mastered"
    if len(deficient) == 1:
        return f"{deficient[0]}-gap"
    return "broad-gap"


# ---- core clustering ------------------------------------------------------

def cluster_students(
    mastery_xlsx: str | Path,
    item_bank_json: str | Path,
    output_xlsx: str | Path,
    summary_xlsx: str | Path,
    *,
    k: int = 3,
    debug_dir: str | Path | None = None,
    partial_threshold: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cluster students by mastery profile. Returns (mastery_with_clusters,
    cluster_summary).
    """
    mastery = pd.read_excel(mastery_xlsx)
    bank = json.loads(Path(item_bank_json).read_text())
    skills: list[str] = bank["meta"]["node_order"]
    prereqs: dict = bank["meta"].get("prerequisites", {})

    missing = [s for s in skills if s not in mastery.columns]
    if missing:
        raise ValueError(f"mastery xlsx missing skill columns: {missing}")

    # Separate scored vs unscored students (any NaN in skill columns => unscored)
    skill_mat = mastery[skills].to_numpy(dtype=float)
    scored = ~np.isnan(skill_mat).any(axis=1)
    X = skill_mat[scored]
    n_scored = X.shape[0]

    if n_scored < k:
        raise ValueError(
            f"only {n_scored} scored students but k={k}; reduce k or score more sheets"
        )

    # --- Hierarchical clustering ---
    Z = linkage(X, method="ward", metric="euclidean")
    raw_labels = fcluster(Z, t=k, criterion="maxclust")   # values in 1..k

    # Re-number so cluster 0 is weakest (lowest mean mastery), k-1 strongest
    cluster_means = {}
    for lab in np.unique(raw_labels):
        cluster_means[lab] = X[raw_labels == lab].mean()
    sorted_labs = sorted(cluster_means, key=cluster_means.get)   # ascending
    relabel = {old: new for new, old in enumerate(sorted_labs)}
    final_labels = np.array([relabel[l] for l in raw_labels])

    # Assign back to original DataFrame: -1 for unscored
    cluster_col = np.full(len(mastery), -1, dtype=int)
    cluster_col[scored] = final_labels
    out = mastery.copy()
    out["cluster"] = cluster_col

    # --- Build per-cluster summary ---
    summary_rows = []
    depths = compute_depths(prereqs, skills) if prereqs else {s: 0 for s in skills}
    for c in range(k):
        members = out[out["cluster"] == c]
        centroid = members[skills].mean()
        deficits = centroid[centroid < partial_threshold].sort_values()
        top3 = list(deficits.index[:3])
        row = {
            "cluster": c,
            "size": len(members),
            "mean_mastery": float(centroid.mean()),
            "label": cluster_label(centroid, depths, partial_threshold),
            "weakest_skills": ", ".join(top3),
        }
        for s in skills:
            row[f"centroid_{s}"] = float(centroid[s])
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)

    # --- Validation: silhouette at k=2..5 to flag whether k is well-chosen ---
    silhouettes = {}
    for kk in range(2, min(6, n_scored)):
        labs = fcluster(Z, t=kk, criterion="maxclust")
        if len(np.unique(labs)) < 2:
            continue
        silhouettes[kk] = float(silhouette_score(X, labs))

    print(f"clustering: {n_scored} scored, {(~scored).sum()} unscored, k={k}")
    print("silhouette scores by k (higher = better-separated clusters):")
    for kk, s in silhouettes.items():
        marker = "  <-- chosen" if kk == k else ""
        print(f"  k={kk}: {s:+.3f}{marker}")
    if k in silhouettes and silhouettes[k] < 0.15:
        print(f"  note: silhouette at k={k} is low; clusters may not be well-separated")

    # --- Write outputs ---
    Path(output_xlsx).parent.mkdir(parents=True, exist_ok=True)
    out.to_excel(output_xlsx, index=False)
    summary.to_excel(summary_xlsx, index=False)
    print(f"\nwrote {output_xlsx}")
    print(f"wrote {summary_xlsx}")
    print("\ncluster summary:")
    for _, r in summary.iterrows():
        print(f"  cluster {r['cluster']} (n={r['size']:>3}, "
              f"mean={r['mean_mastery']:.2f}, label={r['label']}): "
              f"weakest -> {r['weakest_skills']}")

    if debug_dir is not None:
        _write_debug_plots(Z, X, k, skills, summary, Path(debug_dir),
                          silhouettes)

    return out, summary


# ---- diagnostics plots ----------------------------------------------------

def _write_debug_plots(
    Z: np.ndarray, X: np.ndarray, k: int, skills: list[str],
    summary: pd.DataFrame, debug_dir: Path, silhouettes: dict,
) -> None:
    """Three small validation plots: dendrogram, silhouette-by-k,
    cluster centroid heatmap."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    debug_dir.mkdir(parents=True, exist_ok=True)

    # 1) Dendrogram with the k-cut highlighted
    fig, ax = plt.subplots(figsize=(10, 4))
    dendrogram(Z, no_labels=True, color_threshold=Z[-(k - 1), 2], ax=ax)
    ax.axhline(Z[-(k - 1), 2], color="red", linestyle="--", linewidth=0.7,
               label=f"k={k} cut")
    ax.set_title("Hierarchical clustering dendrogram (Ward linkage)")
    ax.set_xlabel("students")
    ax.set_ylabel("merge distance")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(debug_dir / "dendrogram.png", dpi=120)
    plt.close(fig)

    # 2) Silhouette vs k
    if silhouettes:
        fig, ax = plt.subplots(figsize=(5, 3.5))
        ks = sorted(silhouettes.keys())
        ax.plot(ks, [silhouettes[kk] for kk in ks], marker="o", color="#185FA5")
        ax.axvline(k, color="red", linestyle="--", linewidth=0.7,
                   label=f"chosen k={k}")
        ax.set_xlabel("k")
        ax.set_ylabel("silhouette score")
        ax.set_title("silhouette by k")
        ax.set_xticks(ks)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(debug_dir / "silhouette.png", dpi=120)
        plt.close(fig)

    # 3) Centroid heatmap (cluster x skill)
    cent_cols = [c for c in summary.columns if c.startswith("centroid_")]
    M = summary[cent_cols].to_numpy()                       # (k, K)
    fig, ax = plt.subplots(figsize=(max(6, len(skills) * 0.7), 0.8 * k + 1.6))
    im = ax.imshow(M, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
    ax.set_yticks(range(k))
    ax.set_yticklabels([f"c{c} ({summary['label'][c]})" for c in range(k)])
    ax.set_xticks(range(len(skills)))
    ax.set_xticklabels(skills, rotation=45, ha="right", fontsize=9)
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(j, i, f"{M[i, j]:.2f}", ha="center", va="center",
                    color="black", fontsize=8)
    ax.set_title("cluster centroid mastery (rows = clusters, cols = skills)")
    fig.colorbar(im, ax=ax, label="mastery")
    fig.tight_layout()
    fig.savefig(debug_dir / "heatmap.png", dpi=120)
    plt.close(fig)

    print(f"wrote {debug_dir / 'dendrogram.png'}")
    print(f"wrote {debug_dir / 'silhouette.png'}")
    print(f"wrote {debug_dir / 'heatmap.png'}")


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mastery", required=True,
                    help="path to mastery.xlsx from scoring_layer")
    ap.add_argument("--bank", required=True,
                    help="path to item bank JSON")
    ap.add_argument("--output", required=True,
                    help="path to write mastery_with_clusters.xlsx")
    ap.add_argument("--summary", required=True,
                    help="path to write cluster_summary.xlsx")
    ap.add_argument("--k", type=int, default=3, help="number of clusters")
    ap.add_argument("--debug-dir", default=None,
                    help="if set, write dendrogram/silhouette/heatmap PNGs here")
    args = ap.parse_args()

    cluster_students(
        mastery_xlsx=args.mastery,
        item_bank_json=args.bank,
        output_xlsx=args.output,
        summary_xlsx=args.summary,
        k=args.k,
        debug_dir=args.debug_dir,
    )


if __name__ == "__main__":
    _main()