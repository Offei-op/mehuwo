"""Skill diagnosis from per-item correctness.

Default: fixed-parameter DINA inference over the bank's Q-matrix. Outputs
posterior skill probabilities, evidence/status columns, diagnostic details,
follow-up checks, and model assumptions in a multi-sheet workbook.
Use --method simple for the original single-skill average baseline.

Input columns item_1 through item_N refer to bank single_skill_items followed
by multi_skill_items. Column position is irrelevant; printed question order
must match, or responses must be remapped before scoring. Blanks are missing
evidence, 0 is incorrect, and scored=0 ignores the entire student row.
See docs/diagnostics.md for assumptions and limits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assessment.contracts import read_results

try:
    from .diagnostic_model import DinaModel, mastery_status
except ImportError:  # direct CLI invocation
    from diagnostic_model import DinaModel, mastery_status


def score_students(
    results_xlsx: str | Path,
    item_bank_json: str | Path,
    output_xlsx: str | Path,
    *,
    audit_xlsx: str | Path | None = None,
    partial_threshold: float = 0.5,
    mastered_threshold: float = 1.0,
    method: str = "dina",
    slip: float = 0.1,
    guess: float = 0.25,
    prior: float = 0.5,
    confidence: float = 0.8,
    form_manifest_json: str | Path | None = None,
) -> pd.DataFrame:
    """Compute per-skill mastery scores from per-item correctness."""

    bank = json.loads(Path(item_bank_json).read_text(encoding="utf-8"))
    form = json.loads(Path(form_manifest_json).read_text(encoding="utf-8")) if form_manifest_json else None
    results = read_results(results_xlsx, bank, form)

    skills: list[str] = bank["meta"]["node_order"]
    all_items: list[dict] = bank["single_skill_items"] + bank["multi_skill_items"]

    item_cols = [f"item_{i+1}" for i in range(len(all_items))]
    actual_cols = [c for c in results.columns if str(c).startswith("item_")]
    if set(actual_cols) != set(item_cols):
        raise ValueError(
            "Results must have exactly item_1 through item_N matching bank order. "
            f"Missing: {sorted(set(item_cols)-set(actual_cols))}; "
            f"unexpected: {sorted(set(actual_cols)-set(item_cols))}"
        )
    for col in ("student_idx", "student_name"):
        if col not in results or results[col].isna().any():
            raise ValueError(f"Missing student identity: {col}")
    if results["student_idx"].duplicated().any():
        raise ValueError("student_idx must be unique.")
    for col in item_cols:
        values = pd.to_numeric(results[col], errors="raise")
        if not values.dropna().isin([0, 1]).all():
            raise ValueError(f"{col}: expected 0, 1, or blank for missing evidence.")
        results[col] = values
    if "scored" in results and not results["scored"].isin([0, 1]).all():
        raise ValueError("scored must contain only 0 or 1, with no blanks.")
    if method == "dina":
        return _score_dina(results, bank, output_xlsx, audit_xlsx,
                           slip=slip, guess=guess, prior=prior, confidence=confidence)
    if method != "simple":
        raise ValueError("method must be dina or simple")

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


def _score_dina(results, bank, output_xlsx, audit_xlsx, *, slip, guess, prior, confidence):
    if not np.isfinite(confidence) or not 0.5 < confidence < 1:
        raise ValueError("confidence must be strictly between 0.5 and 1.")
    model = DinaModel(bank, slip=slip, guess=guess, prior=prior)
    skills = model.skills
    item_cols = [f"item_{i+1}" for i in range(len(model.items))]
    rows, details, follow_up = [], [], []
    for _, student in results.iterrows():
        identity = {"student_idx": student["student_idx"], "student_name": student["student_name"]}
        responses = student[item_cols].to_numpy(dtype=float)
        if not student.get("scored", 1):
            responses[:] = np.nan
        inference = model.infer(responses)
        statuses = [mastery_status(p, n, 1 - confidence, confidence)
                    for p, n in zip(inference["probability"], inference["evidence"])]
        row = {**identity, **dict(zip(skills, inference["probability"])),
               "diagnostic_method": "dina", "diagnostic_confidence": confidence,
               "n_items_observed": int(inference["observed"].sum()),
               "n_skills_mastered": statuses.count("likely_mastered"),
               "n_skills_uncertain": statuses.count("uncertain"),
               "n_skills_unmastered": statuses.count("likely_not_mastered"),
               "n_skills_unmeasured": statuses.count("insufficient_evidence")}
        row["mean_mastery"] = (float(np.nanmean(inference["probability"]))
                               if inference["evidence"].any() else np.nan)
        for j, skill in enumerate(skills):
            row[f"status_{skill}"] = statuses[j]
            row[f"evidence_{skill}"] = int(inference["evidence"][j])
            details.append({**identity, "skill": skill,
                            "mastery_probability": inference["probability"][j],
                            "status": statuses[j], "entropy_bits": inference["entropy"][j],
                            "observed_items_requiring_skill": int(inference["evidence"][j])})
        rows.append(row)
        needs_check = any(s in {"uncertain", "insufficient_evidence"} for s in statuses)
        if needs_check:
            candidates = model.next_items(inference)
            for rank, candidate in enumerate(candidates, 1):
                follow_up.append({**identity, "rank": rank, **candidate,
                                  "action": "Review missing response; administer if appropriate"})
            if not candidates:
                uncertain = [s for s, status in zip(skills, statuses)
                             if status in {"uncertain", "insufficient_evidence"}]
                follow_up.append({**identity, "rank": 1, "item_id": "", "item_column": "",
                                  "skills": ", ".join(uncertain),
                                  "expected_information_gain_bits": np.nan,
                                  "action": "Prepare new targeted questions; current bank exhausted"})
    mastery = pd.DataFrame(rows, columns=(list(rows[0]) if rows else
                           ["student_idx", "student_name", *skills, "mean_mastery"]))
    diagnostic = pd.DataFrame(details, columns=["student_idx", "student_name", "skill",
                              "mastery_probability", "status", "entropy_bits",
                              "observed_items_requiring_skill"])
    follow = pd.DataFrame(follow_up, columns=["student_idx", "student_name", "rank", "item_id",
                         "item_column", "skills", "expected_information_gain_bits", "action"])
    metadata = pd.DataFrame([
        {"field": "method", "value": "DINA fixed-parameter exact inference v1"},
        {"field": "parameter_status", "value": "Assumed or supplied; not fitted or validated by this run"},
        {"field": "prior", "value": prior},
        {"field": "mastered_probability_threshold", "value": confidence},
        {"field": "nonmastered_probability_threshold", "value": 1 - confidence},
        {"field": "missing", "value": "Blank responses omitted from likelihood; scored=0 ignores entire row"},
        {"field": "q_matrix", "value": "Uses q_vectors as supplied; prerequisite ancestors are not added"},
    ])
    out = Path(output_xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        mastery.to_excel(writer, sheet_name="mastery", index=False)
        diagnostic.to_excel(writer, sheet_name="diagnostics", index=False)
        follow.to_excel(writer, sheet_name="follow_up", index=False)
        metadata.to_excel(writer, sheet_name="model", index=False)
        pd.DataFrame(model.parameters).to_excel(writer, sheet_name="item_parameters", index=False)
    if audit_xlsx is not None:
        Path(audit_xlsx).parent.mkdir(parents=True, exist_ok=True)
        diagnostic.to_excel(audit_xlsx, index=False)
    print(f"wrote {out}: DINA probabilities for {len(mastery)} students")
    print("Parameters are assumed or supplied, not calibrated. Review uncertain skills and follow_up sheet.")
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
    ap.add_argument("--method", choices=["dina", "simple"], default="dina")
    ap.add_argument("--slip", type=float, default=0.1)
    ap.add_argument("--guess", type=float, default=0.25)
    ap.add_argument("--prior", type=float, default=0.5)
    ap.add_argument("--confidence", type=float, default=0.8)
    ap.add_argument("--form-manifest", help="Printed-order results mapping; embedded scan manifests are read automatically")
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
        method=args.method, slip=args.slip, guess=args.guess,
        prior=args.prior, confidence=args.confidence,
        form_manifest_json=args.form_manifest,
    )


if __name__ == "__main__":
    _main()
