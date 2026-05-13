"""Remediation layer.

Takes mastery_with_clusters.xlsx plus the item bank JSON, and produces an
ordered remediation path for each cluster. The order is a topological
sort of the cluster's deficit skills in the prerequisite DAG, with depth
as a tie-breaker so foundational skills come first even when they're not
strict prerequisites of the other deficits.

Inputs:
  - mastery_with_clusters.xlsx (from clustering_layer)
  - item bank JSON with meta.prerequisites

Outputs:
  - cluster_paths.xlsx: long format (cluster, step, skill, depth, type)
      where type is 'foundation' / 'middle' / 'leaf' from the DAG depth
  - optional student_paths.xlsx: same but per-student (much longer)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def compute_depths(prereqs: dict, nodes: list[str]) -> dict[str, int]:
    """Same depth function as clustering_layer: 0 for roots, otherwise
    1 + max parent depth."""
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


def depth_band(d: int, max_depth: int) -> str:
    """Categorise a skill by its DAG depth: foundation / middle / leaf."""
    if max_depth == 0:
        return "foundation"
    if d <= max_depth / 3.0:
        return "foundation"
    if d >= 2.0 * max_depth / 3.0:
        return "leaf"
    return "middle"


def topo_path(
    deficits: list[str], prereqs: dict, depths: dict[str, int],
) -> list[str]:
    """Return deficit skills in topological order, prereqs first, depth
    as tiebreaker.

    Method: Kahn-style traversal of the deficit *subgraph* (edges only
    between deficient skills). Entry points are deficient skills whose
    deficient prerequisites are empty. When multiple entry points are
    available simultaneously, pick the one with the smallest full-DAG
    depth first -- so foundational deficits get addressed before
    parallel mid/leaf deficits even if no strict prerequisite link
    exists in the deficit subgraph.
    """
    deficit_set = set(deficits)
    indeg: dict[str, int] = {s: 0 for s in deficit_set}
    adj: dict[str, list[str]] = {s: [] for s in deficit_set}
    for s in deficit_set:
        for p in prereqs.get(s, []):
            if p in deficit_set:
                indeg[s] += 1
                adj[p].append(s)

    available = sorted([s for s in deficit_set if indeg[s] == 0],
                       key=lambda s: depths.get(s, 0))
    result: list[str] = []
    while available:
        s = available.pop(0)
        result.append(s)
        for c in adj[s]:
            indeg[c] -= 1
            if indeg[c] == 0:
                available.append(c)
        available.sort(key=lambda s: depths.get(s, 0))

    # Sanity: if there's a cycle, some deficits won't appear -- surface that
    missed = deficit_set - set(result)
    if missed:
        # Append remaining (shouldn't happen if bank passes validate_bank)
        result.extend(sorted(missed, key=lambda s: depths.get(s, 0)))
    return result


# ---- main entry point -----------------------------------------------------

def build_remediation_plan(
    mastery_clustered_xlsx: str | Path,
    item_bank_json: str | Path,
    cluster_paths_xlsx: str | Path,
    *,
    student_paths_xlsx: str | Path | None = None,
    partial_threshold: float = 0.5,
) -> pd.DataFrame:
    """Build per-cluster (and optionally per-student) remediation paths."""

    mastery = pd.read_excel(mastery_clustered_xlsx)
    bank = json.loads(Path(item_bank_json).read_text())
    skills: list[str] = bank["meta"]["node_order"]
    prereqs: dict = bank["meta"].get("prerequisites", {})

    if "cluster" not in mastery.columns:
        raise ValueError("input xlsx has no 'cluster' column -- run "
                         "clustering_layer first")

    depths = compute_depths(prereqs, skills)
    max_depth = max(depths.values()) if depths else 0

    # ---- cluster-level paths (one path per cluster, applied to all members) ----
    cluster_ids = sorted(c for c in mastery["cluster"].unique() if c >= 0)
    cluster_rows = []
    for c in cluster_ids:
        members = mastery[mastery["cluster"] == c]
        centroid = members[skills].mean()
        deficits = list(centroid[centroid < partial_threshold].index)
        if not deficits:
            cluster_rows.append({
                "cluster": c, "step": 0, "skill": "(all mastered)",
                "depth": np.nan, "band": "n/a",
                "centroid_mastery": float(centroid.mean()),
                "n_students": len(members),
            })
            continue
        path = topo_path(deficits, prereqs, depths)
        for step, skill in enumerate(path):
            cluster_rows.append({
                "cluster": c,
                "step": step + 1,
                "skill": skill,
                "depth": depths[skill],
                "band": depth_band(depths[skill], max_depth),
                "centroid_mastery": float(centroid[skill]),
                "n_students": len(members),
            })

    cluster_df = pd.DataFrame(cluster_rows)
    Path(cluster_paths_xlsx).parent.mkdir(parents=True, exist_ok=True)
    cluster_df.to_excel(cluster_paths_xlsx, index=False)
    print(f"wrote {cluster_paths_xlsx}")

    print("\nremediation paths by cluster:")
    for c in cluster_ids:
        sub = cluster_df[cluster_df["cluster"] == c]
        members = mastery[mastery["cluster"] == c]
        if sub.empty or sub.iloc[0]["skill"] == "(all mastered)":
            print(f"  cluster {c} (n={len(members)}): all mastered, no remediation")
            continue
        path_str = " -> ".join(sub["skill"].tolist())
        print(f"  cluster {c} (n={len(members)}): {path_str}")

    # ---- per-student paths (optional, much more granular) ----
    if student_paths_xlsx is not None:
        student_rows = []
        for _, row in mastery.iterrows():
            s_idx = int(row["student_idx"])
            s_name = str(row["student_name"])
            s_cluster = int(row["cluster"])
            if s_cluster < 0:
                continue   # unscored students get no path
            s_mastery = row[skills]
            deficits = [s for s in skills if s_mastery[s] < partial_threshold]
            if not deficits:
                student_rows.append({
                    "student_idx": s_idx, "student_name": s_name,
                    "cluster": s_cluster, "step": 0,
                    "skill": "(all mastered)", "depth": np.nan, "band": "n/a",
                    "student_mastery": np.nan,
                })
                continue
            path = topo_path(deficits, prereqs, depths)
            for step, skill in enumerate(path):
                student_rows.append({
                    "student_idx": s_idx,
                    "student_name": s_name,
                    "cluster": s_cluster,
                    "step": step + 1,
                    "skill": skill,
                    "depth": depths[skill],
                    "band": depth_band(depths[skill], max_depth),
                    "student_mastery": float(s_mastery[skill]),
                })
        sdf = pd.DataFrame(student_rows)
        sdf.to_excel(student_paths_xlsx, index=False)
        print(f"wrote {student_paths_xlsx}  ({len(sdf)} step rows)")

    return cluster_df


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mastery-clustered", required=True,
                    help="path to mastery_with_clusters.xlsx")
    ap.add_argument("--bank", required=True, help="path to item bank JSON")
    ap.add_argument("--cluster-paths", required=True,
                    help="path to write cluster_paths.xlsx")
    ap.add_argument("--student-paths", default=None,
                    help="if set, also write per-student paths xlsx")
    ap.add_argument("--partial-threshold", type=float, default=0.5,
                    help="mastery >= this counts as having the skill "
                         "(default 0.5)")
    args = ap.parse_args()

    build_remediation_plan(
        mastery_clustered_xlsx=args.mastery_clustered,
        item_bank_json=args.bank,
        cluster_paths_xlsx=args.cluster_paths,
        student_paths_xlsx=args.student_paths,
        partial_threshold=args.partial_threshold,
    )


if __name__ == "__main__":
    _main()
