"""Synthetic results generator.

The OCR step is not yet built; teachers can't yet upload a scanned
sheet and get a `results.xlsx` out the other side. To keep the UI
demoable end-to-end, this module generates plausible synthetic
per-item correctness from an item bank, given a class size and a
controllable mastery profile.

This is for UI demos and integration testing only. Real teachers
must wait for the OCR pipeline to land.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


# Three example student archetypes -- used by the demo generator to
# produce realistic-looking class data with visible cluster structure.
ARCHETYPES = {
    "struggling": {
        # Foundations weak, downstream skills weaker
        "depth_profile": {0: 0.45, 1: 0.30, 2: 0.18, 3: 0.10},
        "noise": 0.10,
    },
    "developing": {
        # Foundations solid, middle wobbly, leaf weak
        "depth_profile": {0: 0.85, 1: 0.65, 2: 0.45, 3: 0.30},
        "noise": 0.10,
    },
    "strong": {
        "depth_profile": {0: 0.95, 1: 0.90, 2: 0.80, 3: 0.72},
        "noise": 0.08,
    },
}


def _compute_depths(prereqs: dict, nodes: list[str]) -> dict[str, int]:
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


def generate_synthetic_results(
    bank_path: Path,
    output_path: Path,
    *,
    n_students: int = 40,
    archetype_mix: dict[str, float] | None = None,
    unscored_rate: float = 0.02,
    seed: int = 0,
) -> pd.DataFrame:
    """Generate a results.xlsx that the scoring layer will accept.

    Each student is sampled from one of the archetypes (default mix is
    30% struggling / 50% developing / 20% strong). Per-item correctness
    is sampled from a Bernoulli with parameter that depends on whether
    the student has mastered the item's q_vector skills. A few rows are
    flipped to unscored to exercise the NaN-handling pathways.
    """
    rng = np.random.default_rng(seed)
    bank = json.loads(Path(bank_path).read_text())
    nodes: list[str] = bank["meta"]["node_order"]
    prereqs: dict = bank["meta"].get("prerequisites", {})
    depths = _compute_depths(prereqs, nodes)
    all_items = bank["single_skill_items"] + bank["multi_skill_items"]

    archetype_mix = archetype_mix or {
        "struggling": 0.30, "developing": 0.50, "strong": 0.20,
    }
    archetypes = list(archetype_mix.keys())
    probs = np.array([archetype_mix[a] for a in archetypes], dtype=float)
    probs /= probs.sum()

    rows = []
    for i in range(n_students):
        a = rng.choice(archetypes, p=probs)
        profile = ARCHETYPES[a]["depth_profile"]
        noise = ARCHETYPES[a]["noise"]

        # Per-skill mastery for this student
        skill_mastery = {}
        for s in nodes:
            d = depths[s]
            base = profile.get(d, profile[max(profile)])
            skill_mastery[s] = float(
                np.clip(base + rng.normal(0, noise), 0.02, 0.98)
            )

        student_correct = []
        for it in all_items:
            # Probability of correctness = min of mastery on required skills.
            # If any required skill isn't mastered, the item is hard.
            qv = it["q_vector"]
            req = [nodes[idx] for idx, b in enumerate(qv) if b == 1]
            if not req:
                p = 0.5
            else:
                p = min(skill_mastery[s] for s in req)
            # MCQ guessing floor (1/4)
            p = max(p, 0.25)
            student_correct.append(int(rng.random() < p))

        row = {
            "student_idx": i,
            "student_name": f"Student {i+1:03d}",
            "archetype_hint": a,
        }
        for j, v in enumerate(student_correct):
            row[f"item_{j+1}"] = v
        row["scored"] = 0 if rng.random() < unscored_rate else 1
        rows.append(row)

    df = pd.DataFrame(rows)
    # If scored=0, blank out the item columns so the scoring layer's
    # NaN-treatment kicks in correctly.
    item_cols = [c for c in df.columns if c.startswith("item_")]
    df.loc[df["scored"] == 0, item_cols] = 0

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)
    return df
