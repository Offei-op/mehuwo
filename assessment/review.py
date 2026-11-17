"""Immutable bank snapshots and version-specific expert decisions in SQLite."""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

from assessment.contracts import digest, items, now, validate_bank


def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "assessment.sqlite", timeout=15)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS banks (
          version TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY, bank_version TEXT NOT NULL, parent_version TEXT,
          scope TEXT NOT NULL, reviewer TEXT NOT NULL, decision TEXT NOT NULL,
          rationale TEXT NOT NULL, created_at TEXT NOT NULL);
    """)
    return con


def snapshot(root, bank):
    validate_bank(bank)
    version = digest(bank)
    with connect(root) as con:
        con.execute("INSERT OR IGNORE INTO banks VALUES (?, ?, ?)",
                    (version, json.dumps(bank, ensure_ascii=False), now()))
    return version


def record_review(root, parent, revised, *, scope, reviewer, decision, rationale):
    if not reviewer.strip() or not rationale.strip():
        raise ValueError("Reviewer name and rationale are required.")
    if decision not in {"approved", "needs_revision"}:
        raise ValueError("Choose approved or needs_revision.")
    if scope != "skill_map" and scope not in {it["item_id"] for it in items(revised)}:
        raise ValueError("Unknown review target.")
    old = snapshot(root, parent)
    version = snapshot(root, revised)
    with connect(root) as con:
        con.execute("INSERT INTO reviews(bank_version,parent_version,scope,reviewer,decision,rationale,created_at) VALUES(?,?,?,?,?,?,?)",
                    (version, old, scope, reviewer.strip(), decision, rationale.strip(), now()))
    return version


def history(root):
    with connect(root) as con:
        return [dict(r) for r in con.execute("SELECT * FROM reviews ORDER BY id DESC")]


def review_status(root, bank):
    version = digest(bank)
    latest = {}
    for row in history(root):
        if row["bank_version"] == version and row["scope"] not in latest:
            latest[row["scope"]] = row["decision"]
    return [{"scope": scope, "status": latest.get(scope, "not_reviewed")}
            for scope in ["skill_map", *[it["item_id"] for it in items(bank)]]]


def revise_question(bank, item_id, *, stem, options, correct_option, required_skills,
                    solution_expression=""):
    revised = copy.deepcopy(bank)
    matches = [it for it in items(bank) if it["item_id"] == item_id]
    item = copy.deepcopy(matches[0]) if matches else {"item_id": item_id}
    if not item_id.strip():
        raise ValueError("Item ID is required.")
    nodes = bank["meta"]["node_order"]
    if not required_skills or not set(required_skills) <= set(nodes):
        raise ValueError("Select at least one known skill.")
    if correct_option not in list("ABCD") or len(options) != 4:
        raise ValueError("Choose an answer among four options.")
    item.update(stem=stem, options=dict(zip("ABCD", options)), correct_option=correct_option,
                correct_answer=options["ABCD".index(correct_option)],
                q_vector=[int(s in required_skills) for s in nodes])
    item.pop("target_node", None)
    item.pop("target_nodes", None)
    item.pop("distractor_rationales", None)  # old option rationales may no longer apply
    item.pop("diagnostic_parameters", None)  # calibration cannot transfer silently to a revised item
    if solution_expression.strip():
        item["solution_expression"] = solution_expression.strip()
    else:
        item.pop("solution_expression", None)
    target = "single_skill_items" if len(required_skills) == 1 else "multi_skill_items"
    item["target_node" if len(required_skills) == 1 else "target_nodes"] = (
        required_skills[0] if len(required_skills) == 1 else required_skills)
    for category in ("single_skill_items", "multi_skill_items"):
        revised[category] = [it for it in revised[category] if it["item_id"] != item_id]
    revised[target].append(item)
    validate_bank(revised)
    return revised
