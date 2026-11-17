"""Keep machine output, human corrections, and canonical results distinct."""
import json
import uuid
from pathlib import Path

import pandas as pd

from assessment.contracts import digest, now, remap_results, validate_results


def approve_scan(root, raw, corrected, bank, *, reviewer, form=None, source=""):
    if not reviewer.strip():
        raise ValueError("Reviewer name is required.")
    if not raw[["student_idx", "student_name"]].equals(corrected[["student_idx", "student_name"]]):
        raise ValueError("Student identities cannot be changed during scan correction.")
    canonical = remap_results(corrected, bank, form) if form else validate_results(corrected, bank)
    changes = []
    for row in range(len(raw)):
        for col in [c for c in corrected if c == "scored" or str(c).startswith("item_")]:
            a, b = raw.iloc[row][col], corrected.iloc[row][col]
            if (pd.isna(a) and pd.isna(b)) or (pd.notna(a) and pd.notna(b) and a == b):
                continue
            changes.append({"student_idx": corrected.iloc[row].student_idx, "field": col,
                            "before": a, "after": b})
    destination = Path(root) / "scan_reviews" / str(uuid.uuid4())
    destination.mkdir(parents=True)
    with pd.ExcelWriter(destination / "review.xlsx", engine="openpyxl") as writer:
        canonical.to_excel(writer, sheet_name="results", index=False)
        pd.DataFrame([{"bank_version": digest(bank), "column_order": "canonical"}]).to_excel(
            writer, sheet_name="assessment_meta", index=False)
        raw.to_excel(writer, sheet_name="raw_scan", index=False)
        corrected.to_excel(writer, sheet_name="corrected_print_order", index=False)
        pd.DataFrame(changes, columns=["student_idx", "field", "before", "after"]).to_excel(writer, sheet_name="corrections", index=False)
    (destination / "provenance.json").write_text(json.dumps({
        "reviewer": reviewer.strip(), "reviewed_at": now(), "source": source,
        "bank_version": digest(bank), "form_id": form["form_id"] if form else None,
        "raw_digest": digest(json.loads(raw.to_json(orient="records"))),
        "corrected_digest": digest(json.loads(corrected.to_json(orient="records"))),
        "column_order": "canonical bank order in results sheet",
    }, indent=2), encoding="utf-8")
    return destination / "review.xlsx"
