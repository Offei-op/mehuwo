"""Timestamped follow-up sessions with idempotent, auditable responses."""
from __future__ import annotations

import json
import uuid

import numpy as np
import pandas as pd

from assessment.contracts import digest, items, now, validate_bank, validate_results
from assessment.review import connect, snapshot
from recognition.diagnostic_model import DinaModel, mastery_status


def database(root):
    con = connect(root)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
          bank_version TEXT NOT NULL, base_results TEXT NOT NULL, selected_items TEXT NOT NULL,
          parameters TEXT NOT NULL, parent_session TEXT);
        CREATE TABLE IF NOT EXISTS responses (
          id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, student_id TEXT NOT NULL,
          item_id TEXT NOT NULL, correct INTEGER NOT NULL CHECK(correct IN (0,1)),
          recorded_by TEXT NOT NULL, recorded_at TEXT NOT NULL, reason TEXT NOT NULL);
    """)
    return con


def create_session(root, bank, base_results, selected_items, *, title, parent_session=None,
                   parameters=None):
    validate_bank(bank)
    base = validate_results(base_results, bank)
    if not title.strip() or not selected_items or len(selected_items) != len(set(selected_items)):
        raise ValueError("Name the session and select distinct questions.")
    if not set(selected_items) <= {it["item_id"] for it in items(bank)}:
        raise ValueError("Unknown follow-up question.")
    parameters = parameters or {"slip": .1, "guess": .25, "prior": .5}
    DinaModel(bank, **parameters)
    version = snapshot(root, bank)
    sid = str(uuid.uuid4())
    with database(root) as con:
        if parent_session and not con.execute("SELECT 1 FROM sessions WHERE session_id=?", (parent_session,)).fetchone():
            raise ValueError("Unknown parent session.")
        con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)", (
            sid, title.strip(), now(), version, base.to_json(orient="records"),
            json.dumps(selected_items), json.dumps(parameters), parent_session))
    return sid


def sessions(root):
    with database(root) as con:
        return [dict(row) for row in con.execute("SELECT * FROM sessions ORDER BY created_at DESC")]


def load_session(root, sid):
    with database(root) as con:
        row = con.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("Unknown session.")
        payload = con.execute("SELECT payload FROM banks WHERE version=?", (row["bank_version"],)).fetchone()[0]
    session = dict(row)
    return session, json.loads(payload), pd.DataFrame(json.loads(row["base_results"]))


def record_responses(root, sid, student_id, answers, *, reviewer, revise=False, reason=""):
    session, bank, base = load_session(root, sid)
    allowed = set(json.loads(session["selected_items"]))
    if str(student_id) not in set(base.student_idx.astype(str)):
        raise ValueError("Student does not belong to this session.")
    if not reviewer.strip() or not answers:
        raise ValueError("Recorder name and at least one response are required.")
    if not set(answers) <= allowed or any(v not in (0, 1) for v in answers.values()):
        raise ValueError("Responses must be 0/1 for selected session items.")
    count = 0
    with database(root) as con:
        con.execute("BEGIN IMMEDIATE")
        for iid, correct in answers.items():
            previous = con.execute(
                "SELECT correct FROM responses WHERE session_id=? AND student_id=? AND item_id=? ORDER BY id DESC LIMIT 1",
                (sid, str(student_id), iid)).fetchone()
            if previous is not None:
                if previous[0] == correct:
                    continue  # browser rerun / repeated submit is idempotent
                if not revise or not reason.strip():
                    raise ValueError("A response already exists. Enable correction and explain the change.")
            con.execute("INSERT INTO responses(session_id,student_id,item_id,correct,recorded_by,recorded_at,reason) VALUES(?,?,?,?,?,?,?)",
                        (sid, str(student_id), iid, int(correct), reviewer.strip(), now(), reason.strip()))
            count += 1
    return count


def response_history(root, sid):
    with database(root) as con:
        return [dict(r) for r in con.execute("SELECT * FROM responses WHERE session_id=? ORDER BY id", (sid,))]


def session_results(root, sid):
    session, bank, base = load_session(root, sid)
    combined = validate_results(base, bank)
    ids = [it["item_id"] for it in items(bank)]
    for event in response_history(root, sid):
        mask = combined.student_idx.astype(str) == event["student_id"]
        combined.loc[mask, f"item_{ids.index(event['item_id'])+1}"] = event["correct"]
        combined.loc[mask, "scored"] = 1
    return combined


def compare_session(root, sid):
    session, bank, base = load_session(root, sid)
    base = validate_results(base, bank)
    combined = session_results(root, sid)
    model = DinaModel(bank, **json.loads(session["parameters"]))
    cols = [f"item_{i+1}" for i in range(len(items(bank)))]
    rows = []
    for r in range(len(base)):
        before, after = (model.infer(df.iloc[r][cols].to_numpy(float)) for df in (base, combined))
        for j, skill in enumerate(model.skills):
            a, b = before["probability"][j], after["probability"][j]
            rows.append({"student_idx": base.iloc[r].student_idx, "skill": skill,
                         "before": a, "after": b, "change": b-a,
                         "status_before": mastery_status(a, before["evidence"][j]),
                         "status_after": mastery_status(b, after["evidence"][j]),
                         "evidence_before": int(before["evidence"][j]),
                         "evidence_after": int(after["evidence"][j])})
    return pd.DataFrame(rows)


def expand_results(base, original_bank, expanded_bank):
    """Align a baseline with an extended bank, rejecting changed old questions."""
    base = validate_results(base, original_bank)
    original_items = {it["item_id"]: it for it in items(original_bank)}
    updated_items = {it["item_id"]: it for it in items(expanded_bank)}
    if original_bank["meta"]["node_order"] != expanded_bank["meta"]["node_order"]:
        raise ValueError("Follow-up skills must match the baseline skill map.")
    for iid, it in original_items.items():
        if iid not in updated_items or digest(it) != digest(updated_items[iid]):
            raise ValueError("Cannot reuse responses after changing an existing question. Create a new item ID.")
    result = base[["student_idx", "student_name", "scored"]].copy()
    old_ids = list(original_items)
    for j, it in enumerate(items(expanded_bank)):
        result[f"item_{j+1}"] = base[f"item_{old_ids.index(it['item_id'])+1}"] if it["item_id"] in old_ids else np.nan
    return result
