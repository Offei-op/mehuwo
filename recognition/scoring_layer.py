"""Skill scoring layer.

Inputs:
  - results.xlsx from recognition_pipeline: rows = students, columns include
    student_idx, student_name, item_1 .. item_N, scored.
  - item bank JSON: defines skills (meta.node_order), single_skill_items
    (each with target_node), and multi_skill_items (each with target_nodes).

Output:
  - mastery.xlsx: rows = students, one column per skill with a value in
    {0, 0.5, 1} (or NaN if the student wasn't scored). Plus per-student
    summary counts.
  - optional audit.xlsx: long-format breakdown of pure vs multi-skill
    signal per (student, skill), so the teacher can see *why* a score
    landed where it did.

Method (lean version, as discussed):
  Mastery on skill K = mean correctness on the single-skill items whose
  target_node == K. Multi-skill items are NOT used in the primary score
  (they conflate many skills); they appear in the audit so disagreements
  are visible.

Item-order assumption:
  results.xlsx columns item_1 .. item_N correspond to the bank's items in
  the order (single_skill_items + multi_skill_items). The sheet was
  printed in that order; the test paper questions must be numbered to
  match. The layer validates the count but cannot validate the ordering
  -- responsibility lies with whoever prints the test paper.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def score_students(
    results_xlsx: str | Path,
    item_bank_json: str | Path,
    output_xlsx: str | Path,
    *,
    audit_xlsx: str | Path | None = None,
    partial_threshold: float = 0.5,
    mastered_threshold: float = 1.0,
) -> pd.DataFrame:
    """Compute per-skill mastery scores from per-item correctness."""

    results = pd.read_excel(results_xlsx)
    bank = json.loads(Path(item_bank_json).read_text())

    skills: list[str] = bank["meta"]["node_order"]
    all_items: list[dict] = bank["single_skill_items"] + bank["multi_skill_items"]

    item_cols = [c for c in results.columns if c.startswith("item_")]
    if len(item_cols) != len(all_items):
        raise ValueError(
            f"results has {len(item_cols)} item columns but bank has "
            f"{len(all_items)} items -- item order/count mismatch"
        )

    # skill -> list of result column names for pure (single-skill) items
    pure_cols: dict[str, list[str]] = {s: [] for s in skills}
    # skill -> list of result column names for multi-skill items that touch it
    multi_cols: dict[str, list[str]] = {s: [] for s in skills}

    for i, it in enumerate(all_items):
        col = item_cols[i]
        target = it.get("target_node")
        if target is not None:                     # single-skill item
            pure_cols[target].append(col)
        for t in it.get("target_nodes", []):       # multi-skill item
            multi_cols[t].append(col)

    # `scored` is set by recognition: 1 if any cell on that row was filled in
    # (i.e. the page covering this student was processed). Unscored students
    # come through with all-zero correctness, which we must not confuse with
    # zero mastery -- so we set their mastery to NaN.
    scored_mask = (
        results["scored"].astype(bool)
        if "scored" in results.columns
        else pd.Series([True] * len(results))
    )

    mastery = pd.DataFrame({
        "student_idx": results["student_idx"].values,
        "student_name": results["student_name"].values,
    })

    skills_with_pure: list[str] = []
    for skill in skills:
        cols = pure_cols[skill]
        if not cols:
            mastery[skill] = np.nan
            continue
        score = results[cols].mean(axis=1)         # in [0, 1]
        score = score.where(scored_mask, np.nan)   # NaN for unscored students
        mastery[skill] = score
        skills_with_pure.append(skill)

    # Per-student summary
    if skills_with_pure:
        sub = mastery[skills_with_pure]
        mastery["n_skills_mastered"] = (sub >= mastered_threshold).sum(axis=1)
        mastery["n_skills_partial"] = (
            (sub >= partial_threshold) & (sub < mastered_threshold)
        ).sum(axis=1)
        mastery["n_skills_unmastered"] = (sub < partial_threshold).sum(axis=1)
        mastery["mean_mastery"] = sub.mean(axis=1)

    out_path = Path(output_xlsx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mastery.to_excel(out_path, index=False)
    print(f"wrote {out_path}  "
          f"({scored_mask.sum()}/{len(results)} students scored, "
          f"{len(skills_with_pure)}/{len(skills)} skills measurable)")

    # Class-wide summary to stdout
    print("\nper-skill mastery rate (across scored students):")
    print(f"  {'skill':<24} {'pure_n':<7} {'multi_n':<8} "
          f"{'%mastered':<11} {'%partial':<10} {'%not':<6}")
    for skill in skills:
        pn = len(pure_cols[skill])
        mn = len(multi_cols[skill])
        if pn == 0 or not scored_mask.any():
            print(f"  {skill:<24} {pn:<7} {mn:<8} {'-':<11} {'-':<10} {'-':<6}")
            continue
        col = mastery.loc[scored_mask, skill]
        pct_m = 100 * (col >= mastered_threshold).mean()
        pct_p = 100 * ((col >= partial_threshold) & (col < mastered_threshold)).mean()
        pct_n = 100 * (col < partial_threshold).mean()
        print(f"  {skill:<24} {pn:<7} {mn:<8} "
              f"{pct_m:>6.1f}%    {pct_p:>5.1f}%    {pct_n:>4.1f}%")

    if audit_xlsx is not None:
        _write_audit(results, item_cols, skills, pure_cols, multi_cols,
                     scored_mask, audit_xlsx)

    return mastery


def _write_audit(
    results: pd.DataFrame,
    item_cols: list[str],
    skills: list[str],
    pure_cols: dict[str, list[str]],
    multi_cols: dict[str, list[str]],
    scored_mask: pd.Series,
    audit_xlsx: str | Path,
) -> None:
    """Long-format breakdown so the teacher can audit any score.

    One row per (student, skill) with: pure_correct/pure_total,
    multi_correct/multi_total, pure_score, multi_score, agreement flag.
    `agreement` is 'both_pass' if pure mastered and multi >=50%,
    'both_fail' if neither, 'pure_pass_multi_fail' when they contradict
    (a common pattern when a student knows the isolated skill but fails
    to apply it in a chain), and the reverse case. NaN when either side
    has no items.
    """
    rows = []
    for s_idx in range(len(results)):
        student_idx = int(results.iloc[s_idx]["student_idx"])
        student_name = str(results.iloc[s_idx]["student_name"])
        scored = bool(scored_mask.iloc[s_idx])
        for skill in skills:
            p_cols = pure_cols[skill]
            m_cols = multi_cols[skill]
            if scored:
                p_correct = int(results.iloc[s_idx][p_cols].sum()) if p_cols else 0
                m_correct = int(results.iloc[s_idx][m_cols].sum()) if m_cols else 0
            else:
                p_correct = m_correct = 0
            p_total, m_total = len(p_cols), len(m_cols)
            p_score = p_correct / p_total if p_total and scored else np.nan
            m_score = m_correct / m_total if m_total and scored else np.nan

            if np.isnan(p_score) or np.isnan(m_score):
                agreement = ""
            else:
                p_ok = p_score >= 0.5
                m_ok = m_score >= 0.5
                if p_ok and m_ok:       agreement = "both_pass"
                elif (not p_ok) and (not m_ok): agreement = "both_fail"
                elif p_ok and not m_ok: agreement = "pure_pass_multi_fail"
                else:                   agreement = "pure_fail_multi_pass"

            rows.append({
                "student_idx": student_idx,
                "student_name": student_name,
                "skill": skill,
                "scored": int(scored),
                "pure_correct": p_correct,
                "pure_total": p_total,
                "pure_score": p_score,
                "multi_correct": m_correct,
                "multi_total": m_total,
                "multi_score": m_score,
                "agreement": agreement,
            })

    audit = pd.DataFrame(rows)
    out = Path(audit_xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    audit.to_excel(out, index=False)
    print(f"wrote {out}  ({len(audit)} student-skill rows)")


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", required=True,
                    help="path to recognition results.xlsx")
    ap.add_argument("--bank", required=True,
                    help="path to item bank JSON")
    ap.add_argument("--output", required=True,
                    help="path to write mastery.xlsx")
    ap.add_argument("--audit", default=None,
                    help="if set, write a long-format audit xlsx here")
    ap.add_argument("--partial-threshold", type=float, default=0.5,
                    help="mastery >= this is at least partial (default 0.5)")
    ap.add_argument("--mastered-threshold", type=float, default=1.0,
                    help="mastery >= this is full mastery (default 1.0)")
    args = ap.parse_args()

    score_students(
        results_xlsx=args.results,
        item_bank_json=args.bank,
        output_xlsx=args.output,
        audit_xlsx=args.audit,
        partial_threshold=args.partial_threshold,
        mastered_threshold=args.mastered_threshold,
    )


if __name__ == "__main__":
    _main()
