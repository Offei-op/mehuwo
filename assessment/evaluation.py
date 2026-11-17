"""Held-out prediction, independent skill judgments, and parameter sensitivity.

No fitting or hyperparameter selection is performed on the held-out responses.
Simple baseline: raw single-target averages, 0.5 for unmeasured skills, product
of required skill estimates for an item. This prediction rule is explicit and
does not turn the baseline into a calibrated diagnostic model.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from assessment.contracts import digest, items, now, validate_results, read_results
from recognition.diagnostic_model import DinaModel

DEFAULT_CONFIGS = [
    {"name": "dina_default", "slip": 0.10, "guess": 0.25, "prior": 0.50},
    {"name": "slip_low", "slip": 0.05, "guess": 0.25, "prior": 0.50},
    {"name": "slip_high", "slip": 0.20, "guess": 0.25, "prior": 0.50},
    {"name": "guess_low", "slip": 0.10, "guess": 0.15, "prior": 0.50},
    {"name": "guess_high", "slip": 0.10, "guess": 0.35, "prior": 0.50},
    {"name": "prior_low", "slip": 0.10, "guess": 0.25, "prior": 0.30},
    {"name": "prior_high", "slip": 0.10, "guess": 0.25, "prior": 0.70},
]


def simple_skills(bank, responses):
    return np.array([
        np.mean([r for it, r in zip(items(bank), responses)
                 if it.get("target_node") == skill and np.isfinite(r)])
        if any(it.get("target_node") == skill and np.isfinite(r)
               for it, r in zip(items(bank), responses)) else np.nan
        for skill in bank["meta"]["node_order"]
    ])


def metrics(frame):
    y, p = frame.actual.to_numpy(float), frame.predicted.to_numpy(float)
    clipped = np.clip(p, 1e-9, 1-1e-9)
    return {"n": len(frame), "brier": float(np.mean((p-y)**2)),
            "log_loss": float(-np.mean(y*np.log(clipped)+(1-y)*np.log1p(-clipped))),
            "accuracy_at_0_5": float(np.mean((p >= .5) == y))}


def evaluate(bank, results, *, seed=42, holdout_fraction=0.25, configs=None,
             skill_reference=None, reference_source=""):
    if not 0 < holdout_fraction < 1:
        raise ValueError("Holdout fraction must be between 0 and 1.")
    frame = validate_results(results, bank)
    cols = [f"item_{i+1}" for i in range(len(items(bank)))]
    original = frame[cols].to_numpy(float)
    train = original.copy()
    rng = np.random.default_rng(seed)
    split = []
    for r, row in enumerate(original):
        observed = np.flatnonzero(np.isfinite(row))
        if len(observed) < 2:
            continue
        n = min(len(observed)-1, max(1, int(round(len(observed)*holdout_fraction))))
        held = set(rng.choice(observed, size=n, replace=False).tolist())
        for j in observed:
            split.append({"row": r, "student_idx": frame.iloc[r].student_idx,
                          "item_index": int(j), "item_id": items(bank)[j]["item_id"],
                          "partition": "test" if j in held else "train"})
        train[r, list(held)] = np.nan
    split_frame = pd.DataFrame(split)
    if split_frame.empty:
        raise ValueError("Need at least one student with two observed responses.")
    tests = split_frame[split_frame.partition == "test"]
    configurations = copy.deepcopy(DEFAULT_CONFIGS if configs is None else configs)
    names = [c["name"] for c in configurations]
    if not names or len(names) != len(set(names)) or "simple" in names:
        raise ValueError("Supply uniquely named DINA configurations (not 'simple').")
    # Explicitly control all item parameters for this sensitivity experiment.
    experiment_bank = copy.deepcopy(bank)
    for it in items(experiment_bank):
        it.pop("diagnostic_parameters", None)
    models = {c["name"]: DinaModel(experiment_bank, **{k: c[k] for k in ("slip", "guess", "prior")})
              for c in configurations}
    q = next(iter(models.values())).q
    predictions = []
    for r, student_tests in tests.groupby("row"):
        skill = np.nan_to_num(simple_skills(bank, train[r]), nan=0.5)
        simple_prediction = np.prod(np.where(q, skill[None, :], 1), axis=1)
        estimates = {"simple": simple_prediction}
        for name, model in models.items():
            inference = model.infer(train[r])
            estimates[name] = inference["posterior"] @ model.correct
        for _, test in student_tests.iterrows():
            j = int(test.item_index)
            for name, probs in estimates.items():
                predictions.append({"method": name, "student_idx": test.student_idx,
                                    "item_id": test.item_id, "actual": original[r, j],
                                    "predicted": float(probs[j])})
    prediction_frame = pd.DataFrame(predictions)
    metric_rows = [{"task": "held_out_response", "method": name, **metrics(group)}
                   for name, group in prediction_frame.groupby("method")]
    full_estimates, sensitivity = {}, []
    for r in range(len(frame)):
        full_estimates[r] = {"simple": simple_skills(bank, original[r])}
        full_estimates[r].update({name: model.infer(original[r])["probability"] for name, model in models.items()})
        for name, values in full_estimates[r].items():
            for skill, value in zip(bank["meta"]["node_order"], values):
                sensitivity.append({"method": name, "student_idx": frame.iloc[r].student_idx,
                                    "skill": skill, "estimate": value,
                                    "input_scope": "full diagnostic responses; not held-out evaluation"})
    judgments = []
    if skill_reference is not None:
        required = {"student_idx", "skill", "mastered", "assessment_id"}
        if not reference_source.strip() or not required <= set(skill_reference):
            raise ValueError("Independent references require source description and student_idx, skill, mastered, assessment_id.")
        ref = skill_reference.copy()
        if (ref.empty or ref[list(required)].isna().any().any() or not ref.mastered.isin([0, 1]).all()
                or ref.assessment_id.astype(str).str.strip().eq("").any()):
            raise ValueError("Reference judgments require complete identifiers and binary mastery.")
        if ref.duplicated(["student_idx", "skill"]).any():
            raise ValueError("Duplicate reference student/skill pairs.")
        student_rows = {str(v): i for i, v in enumerate(frame.student_idx)}
        skills = bank["meta"]["node_order"]
        for _, criterion in ref.iterrows():
            if str(criterion.student_idx) not in student_rows or criterion.skill not in skills:
                raise ValueError("Reference contains a student or skill absent from this diagnosis.")
            r, k = student_rows[str(criterion.student_idx)], skills.index(criterion.skill)
            estimates = full_estimates[r]
            for name, values in estimates.items():
                judgments.append({"method": name, "student_idx": criterion.student_idx,
                                  "skill": criterion.skill, "assessment_id": criterion.assessment_id,
                                  "actual": criterion.mastered, "predicted": values[k]})
        # Paired comparison: report metrics on student/skill cases measured by every method.
        jf = pd.DataFrame(judgments)
        complete = jf.groupby(["student_idx", "skill"]).predicted.transform(lambda s: s.notna().all())
        jf["paired_case"] = complete
        for name, group in jf.groupby("method"):
            paired = group[group.paired_case]
            if len(paired):
                metric_rows.append({"task": "independent_skill_judgment", "method": name,
                                    **metrics(paired), "coverage": float(group.predicted.notna().mean()),
                                    "decisive_fraction": float(((paired.predicted <= .2) | (paired.predicted >= .8)).mean())})
        judgment_frame = jf
    else:
        judgment_frame = pd.DataFrame(columns=["method", "student_idx", "skill", "assessment_id", "actual", "predicted", "paired_case"])
    metadata = {"created_at": now(), "bank_version": digest(bank), "seed": seed,
                "source_results_digest": digest(json.loads(frame.to_json(orient="records"))),
                "reference_digest": digest(json.loads(skill_reference.to_json(orient="records"))) if skill_reference is not None else "none",
                "holdout_fraction": holdout_fraction, "reference_source": reference_source,
                "reference_protocol": "Independent skill criterion; full diagnostic inputs used for skill comparison",
                "sensitivity_protocol": "Global parameters override per-item values; fixed configurations, no fitting",
                "excluded_students_fewer_than_two_responses": int((np.isfinite(original).sum(axis=1) < 2).sum()),
                "interpretation": "Descriptive evaluation, not classroom validation or evidence of causation"}
    return {"metrics": pd.DataFrame(metric_rows), "predictions": prediction_frame,
            "diagnostic_sensitivity": pd.DataFrame(sensitivity),
            "split": split_frame, "skill_judgments": judgment_frame,
            "configurations": pd.DataFrame(configurations),
            "metadata": pd.DataFrame([{"field": k, "value": v} for k, v in metadata.items()])}


def write_report(report, output):
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, data in report.items():
            data.to_excel(writer, sheet_name=name, index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reference")
    parser.add_argument("--reference-source", default="")
    args = parser.parse_args()
    bank = json.loads(Path(args.bank).read_text(encoding="utf-8"))
    write_report(evaluate(bank, read_results(args.results, bank), seed=args.seed,
                          skill_reference=pd.read_excel(args.reference) if args.reference else None,
                          reference_source=args.reference_source), args.output)
