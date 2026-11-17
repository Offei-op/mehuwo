"""Stable identities and explicit mappings between printed forms and banks."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from curriculum.validate_bank import validate


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def items(bank):
    return bank["single_skill_items"] + bank["multi_skill_items"]


def answer_options(item):
    opts = item["options"]
    if isinstance(opts, dict):
        if set(opts) != set("ABCD"):
            raise ValueError("Options must have exactly A, B, C, D.")
        answer = item.get("correct_option")
        opts = [opts[k] for k in "ABCD"]
        if answer not in list("ABCD"):
            raise ValueError("Select a valid answer letter.")
        index = "ABCD".index(answer)
    else:
        if not isinstance(opts, list) or len(opts) != 4:
            raise ValueError("Four options are required.")
        if item.get("correct_answer") not in opts:
            raise ValueError("The answer must match one option.")
        index = opts.index(item["correct_answer"])
    if any(not isinstance(v, str) or not v.strip() for v in opts) or len(set(opts)) != 4:
        raise ValueError("Options must be distinct, nonempty text.")
    return opts, index


def validate_bank(bank):
    errors, _ = validate(bank)
    if errors:
        raise ValueError("; ".join(errors))
    if not items(bank):
        raise ValueError("Bank must contain questions.")
    for item in items(bank):
        if not item.get("stem", "").strip() or not any(item["q_vector"]):
            raise ValueError("Every question needs a stem and required skills.")
        answer_options(item)


def validate_results(frame, bank):
    """Canonical bank-order correctness, permitting missing evidence."""
    expected = [f"item_{i+1}" for i in range(len(items(bank)))]
    if set(c for c in frame if str(c).startswith("item_")) != set(expected):
        raise ValueError("Results must contain exactly item_1 through item_N for this bank.")
    frame = frame.copy()
    if frame.empty or any(c not in frame for c in ("student_idx", "student_name")):
        raise ValueError("Results need students, student_idx, and student_name.")
    if frame[["student_idx", "student_name"]].isna().any().any() or frame.student_idx.duplicated().any():
        raise ValueError("Student IDs must be unique and identities cannot be blank.")
    for c in expected:
        frame[c] = pd.to_numeric(frame[c], errors="raise")
        if not frame[c].dropna().isin([0, 1]).all():
            raise ValueError(f"{c}: use 0, 1, or blank.")
    if "scored" not in frame:
        frame["scored"] = 1
    if not frame.scored.isin([0, 1]).all():
        raise ValueError("scored must be 0 or 1.")
    frame.loc[frame.scored == 0, expected] = np.nan
    return frame[["student_idx", "student_name", *expected, "scored"]]


def make_form(bank, printed_items):
    """printed_items use renderer's normalized list options."""
    ids = [it["item_id"] for it in printed_items]
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= {it["item_id"] for it in items(bank)}:
        raise ValueError("Printed forms require distinct, known item IDs.")
    mapping = [{"position": i + 1, "item_id": it["item_id"],
                "option_order": it["options"],
                "correct_letter": "ABCD"[it["options"].index(it["correct_answer"])]}
               for i, it in enumerate(printed_items)]
    payload = {"bank_version": digest(bank), "items": mapping}
    return {**payload, "form_id": digest(payload)[:20], "created_at": now()}


def remap_results(frame, bank, form):
    """Map correctness columns in printed order into canonical bank order."""
    if form["bank_version"] != digest(bank):
        raise ValueError("This form belongs to another bank version.")
    payload = {k: form[k] for k in ("bank_version", "items")}
    if digest(payload)[:20] != form["form_id"]:
        raise ValueError("Form manifest identity check failed.")
    entries = form["items"]
    ids = [it["item_id"] for it in items(bank)]
    form_ids = [e["item_id"] for e in entries]
    if len(set(form_ids)) != len(form_ids) or not set(form_ids) <= set(ids):
        raise ValueError("Form contains unknown or duplicate item IDs.")
    if [e["position"] for e in entries] != list(range(1, len(entries) + 1)):
        raise ValueError("Form positions must be consecutive.")
    expected = {f"item_{i+1}" for i in range(len(entries))}
    if {c for c in frame if str(c).startswith("item_")} != expected:
        raise ValueError("Results columns do not match the printed form.")
    output = frame[[c for c in ("student_idx", "student_name", "scored") if c in frame]].copy()
    for i, iid in enumerate(ids):
        output[f"item_{i+1}"] = (frame[f"item_{form_ids.index(iid)+1}"]
                                  if iid in form_ids else np.nan)
    return validate_results(output, bank)


def read_results(source, bank, form=None):
    """Read stamped canonical or printed-order workbooks without double mapping.

    Legacy unstamped inputs are interpreted as canonical unless a form is supplied.
    """
    with pd.ExcelFile(source) as workbook:
        frame = pd.read_excel(workbook)
        if "assessment_meta" in workbook.sheet_names:
            meta = pd.read_excel(workbook, sheet_name="assessment_meta").iloc[0]
            if meta["bank_version"] != digest(bank):
                raise ValueError("Results belong to a different bank version.")
            if meta["column_order"] != "canonical" or form is not None:
                raise ValueError("Canonical results must not be remapped again.")
        else:
            if "form_manifest" in workbook.sheet_names:
                embedded = json.loads(pd.read_excel(workbook, sheet_name="form_manifest").iloc[0]["json"])
                if form is not None and form["form_id"] != embedded["form_id"]:
                    raise ValueError("Supplied and embedded forms disagree.")
                form = embedded
            if form is not None:
                return remap_results(frame, bank, form)
    return validate_results(frame, bank)
