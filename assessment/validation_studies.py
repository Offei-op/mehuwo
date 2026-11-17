"""Versioned curriculum benchmarks and prospective classroom pilot records."""
import json
import io
import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd

from assessment.contracts import digest, now, items, validate_results
from assessment.curriculum_maps import get_map, source_sections, validate_spec
from assessment.review import connect, snapshot, review_status
from assessment.evaluation import evaluate
from assessment.provenance import source_snapshot
from assessment.storage import atomic_bytes, atomic_json


def database(root):
    con = connect(root)
    con.execute("CREATE TABLE IF NOT EXISTS validation_records (version TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
    return con


def save(root, kind, payload):
    version = digest({"kind": kind, "payload": payload})
    with database(root) as con:
        con.execute("INSERT OR IGNORE INTO validation_records VALUES(?,?,?,?)", (version, kind, json.dumps(payload, ensure_ascii=False), now()))
    return version


def records(root, kind):
    with database(root) as con:
        return [dict(r) for r in con.execute("SELECT * FROM validation_records WHERE kind=? ORDER BY created_at DESC", (kind,))]


def load(root, version, kind):
    with database(root) as con:
        row = con.execute("SELECT * FROM validation_records WHERE version=? AND kind=?", (version, kind)).fetchone()
    if row is None:
        raise ValueError("Unknown study record or wrong record type.")
    record = dict(row)
    record["payload"] = json.loads(record["payload"])
    if digest({"kind": kind, "payload": record["payload"]}) != version:
        raise ValueError("Study record content has changed.")
    return record


def create_case(root, *, source_id, selected_sections, scope, reference, reviewer, independent, split):
    validate_spec(reference)
    sections = [s for s in source_sections(root, source_id) if s["id"] in selected_sections]
    if not scope.strip() or not reviewer.strip() or not independent or split not in {"development", "evaluation"}:
        raise ValueError("Name the unit and expert, confirm an independently authored reference, and choose a split.")
    if not sections or len(sections) != len(set(selected_sections)):
        raise ValueError("Choose valid curriculum sections.")
    return save(root, "benchmark_case", {"source_id": source_id, "sections": sections, "scope": scope.strip(),
                "reference": reference, "reviewer": reviewer.strip(), "independent": True, "split": split})


def claims(proposal):
    return {**{f"skill:{s['skill_id']}": s for s in proposal["skills"]},
            **{f"edge:{e['prerequisite']}->{e['skill']}": e for e in proposal["prerequisites"]}}


def ratio(n, d):
    return n / d if d else None


def benchmark(root, case_version, candidate_version, alignment, evidence, *, reviewer, minutes, notes):
    case_record = load(root, case_version, "benchmark_case")
    case = case_record["payload"]
    candidate = get_map(root, candidate_version)
    if (candidate["source_id"] != case["source_id"] or
            set(json.loads(candidate["selected_sections"])) != {s["id"] for s in case["sections"]} or
            candidate["scope"].strip() != case["scope"]):
        raise ValueError("Candidate must use the same source version, sections and unit as the benchmark case.")
    if not reviewer.strip() or not notes.strip() or not 0 < minutes < 10000:
        raise ValueError("Record reviewer, review minutes and correction notes.")
    predicted = json.loads(candidate["spec"])
    reference = case["reference"]
    if set(alignment) != set(predicted["node_order"]):
        raise ValueError("Explicitly align every candidate skill; use null for an unmatched skill.")
    mapped = [v for v in alignment.values() if v is not None]
    if len(mapped) != len(set(mapped)) or not set(mapped) <= set(reference["node_order"]):
        raise ValueError("Use a one-to-one alignment to known reference skills; do not hide split/merged skills.")
    proposal = json.loads(candidate["proposal"])
    explicit = {key for key, claim in claims(proposal).items() if claim["basis"] == "explicit"}
    if set(evidence) != explicit or not set(evidence.values()) <= {"supported", "unsupported", "uncertain"}:
        raise ValueError("Judge every explicit source claim as supported, unsupported or uncertain.")
    reference_edges = {(p, s) for s, parents in reference["prerequisites"].items() for p in parents}
    candidate_edges = [(p, s) for s, parents in predicted["prerequisites"].items() for p in parents]
    matched_edges = {(alignment[p], alignment[s]) for p, s in candidate_edges
                     if alignment[p] is not None and alignment[s] is not None} & reference_edges
    metrics = {"skill_precision": ratio(len(mapped), len(alignment)), "skill_recall": ratio(len(mapped), len(reference["node_order"])),
               "edge_precision": ratio(len(matched_edges), len(candidate_edges)), "edge_recall": ratio(len(matched_edges), len(reference_edges)),
               "explicit_claim_support_fraction": ratio(sum(v == "supported" for v in evidence.values()), len(explicit)),
               "uncertain_claims": sum(v == "uncertain" for v in evidence.values()), "review_minutes": minutes,
               "missing_skills": sorted(set(reference["node_order"]) - set(mapped)),
               "extra_skills": [k for k, v in alignment.items() if v is None],
               "missing_edges": sorted(reference_edges - matched_edges)}
    code, archive = source_snapshot()
    payload = {"source_sha256": code["source_sha256"], "app_version": code["app_version"], "case_version": case_version, "candidate_version": candidate_version, "split": case["split"],
               "reference_precedes_candidate": case_record["created_at"] < candidate["created_at"],
               "alignment": alignment, "evidence": evidence, "reviewer": reviewer.strip(), "notes": notes,
               "metrics": metrics, "scoring_protocol": "one-to-one-expert-alignment-v1"}
    version = save(root, "benchmark_review", payload)
    folder = Path(root) / "benchmark_reviews" / version
    if not (folder / "source.zip").exists():
        atomic_bytes(folder / "source.zip", archive)
        atomic_json(folder / "review.json", payload)
    return version, payload


def create_protocol(root, bank, *, title, owner, cohort, reference_assessment_id, reference_item_ids,
                    reference_procedure, independent, seed=42, holdout_fraction=.25):
    text = (title, owner, cohort, reference_assessment_id, reference_procedure)
    if not all(isinstance(t, str) and t.strip() for t in text) or not independent:
        raise ValueError("Complete the pilot and independent-assessment protocol before freezing it.")
    bank_version = snapshot(root, bank)
    if not reference_item_ids or len(set(reference_item_ids)) != len(reference_item_ids) or any(not isinstance(i, str) or not i.strip() for i in reference_item_ids):
        raise ValueError("List distinct independent-assessment item identifiers.")
    if set(reference_item_ids) & {i["item_id"] for i in items(bank)}:
        raise ValueError("Independent assessment cannot reuse diagnostic item identifiers.")
    if not 0 < holdout_fraction < 1 or not isinstance(seed, int) or seed < 0:
        raise ValueError("Use a nonnegative seed and holdout fraction between zero and one.")
    return save(root, "pilot_protocol", {"title": title, "owner": owner, "cohort": cohort, "bank_version": bank_version,
                "reference_assessment_id": reference_assessment_id, "reference_item_ids": reference_item_ids,
                "reference_procedure": reference_procedure, "independent": True, "seed": seed,
                "holdout_fraction": holdout_fraction, "analysis": "fixed simple-vs-DINA sensitivity; descriptive pilot, no causal learning-gain claim"})


def run_pilot(root, protocol_version, bank, results, reference, *, diagnostic_assessment_id, collected_at, assessor_blinded):
    protocol_record = load(root, protocol_version, "pilot_protocol")
    protocol = protocol_record["payload"]
    if digest(bank) != protocol["bank_version"]:
        raise ValueError("The bank changed after the pilot protocol was frozen.")
    if any(r["status"] != "approved" for r in review_status(root, bank)):
        raise ValueError("Review and approve every question and the skill map for this exact bank version first.")
    if not diagnostic_assessment_id.strip() or diagnostic_assessment_id == protocol["reference_assessment_id"] or not assessor_blinded:
        raise ValueError("Use separate assessment IDs and confirm the reference assessor did not see diagnostic outputs.")
    def timestamp(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Assessment timestamps require a timezone, such as +00:00.")
        if parsed < datetime.fromisoformat(protocol_record["created_at"].replace("Z", "+00:00")):
            raise ValueError("This prospective protocol must be frozen before data collection.")
        if parsed > datetime.fromisoformat(now().replace("Z", "+00:00")):
            raise ValueError("Collection timestamps cannot be in the future.")
        return parsed
    timestamp(collected_at)
    required = {"student_idx", "skill", "mastered", "assessment_id", "collected_at", "assessor"}
    if not required <= set(reference) or reference.empty or reference[list(required)].isna().any().any():
        raise ValueError("Reference rows require student_idx, skill, mastered, assessment_id, collected_at and assessor.")
    if not reference.assessment_id.eq(protocol["reference_assessment_id"]).all() or reference.assessor.astype(str).str.strip().eq("").any():
        raise ValueError("Reference assessment IDs or assessor names do not match the protocol.")
    for value in reference.collected_at:
        timestamp(value)
    frame = validate_results(results, bank)
    if not set(reference.student_idx) <= set(frame.student_idx) or not set(reference.skill) <= set(bank["meta"]["node_order"]):
        raise ValueError("Reference rows contain unknown student or skill identifiers.")
    report = evaluate(bank, frame, seed=protocol["seed"], holdout_fraction=protocol["holdout_fraction"],
                      skill_reference=reference, reference_source=protocol["reference_procedure"])
    inputs = {"protocol_version": protocol_version, "diagnostic_assessment_id": diagnostic_assessment_id,
              "collected_at": collected_at, "assessor_blinded": True,
              "results": json.loads(frame.to_json(orient="records")), "reference": json.loads(reference.to_json(orient="records"))}
    code, archive = source_snapshot()
    inputs["source_sha256"] = code["source_sha256"]
    version = digest(inputs)
    folder = Path(root) / "pilot_evaluations" / version
    if not (folder / "manifest.json").exists():
        folder.mkdir(parents=True, exist_ok=True)
        atomic_bytes(folder / "source.zip", archive)
        atomic_json(folder / "inputs.json", inputs)
        atomic_json(folder / "bank.json", bank)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            for name, frame in report.items():
                frame.to_excel(writer, sheet_name=name, index=False)
        atomic_bytes(folder / "evaluation.xlsx", buffer.getvalue())
        atomic_json(folder / "manifest.json", {"run_version": version, "created_at": now(), "code": code,
                    "protocol_version": protocol_version, "report_sha256": hashlib.sha256((folder / 'evaluation.xlsx').read_bytes()).hexdigest()})
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256((folder / "evaluation.xlsx").read_bytes()).hexdigest() != manifest["report_sha256"]:
        raise ValueError("Saved pilot report was modified.")
    save(root, "pilot_run", {"run_version": version, "protocol_version": protocol_version})
    return folder
