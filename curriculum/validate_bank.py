"""Validate an item bank JSON against the schema the pipeline expects.

The bank is the single source of truth that drives sheet generation,
recognition, scoring, clustering, and remediation. This validator runs a
set of structural and semantic checks so a malformed bank fails loudly at
edit time rather than producing silently-wrong diagnoses downstream.

Schema (required fields):
  meta.subject              : str
  meta.node_order           : list[str], no duplicates -- the skills
  single_skill_items        : list of items, each with:
      item_id, q_vector (list[0/1] aligned to node_order), target_node
  multi_skill_items         : list of items, each with:
      item_id, q_vector, target_nodes (list of >= 2 strings)

Schema (optional but recommended):
  meta.prerequisites        : dict[skill -> list[skill]]  (the DAG; child -> parents)
  meta.short_labels         : dict[skill -> str]  (for compact column headers)
  meta.curriculum_context, meta.design_notes, meta.q_vector_convention

Checks:
  S01  node_order is a list of unique non-empty strings
  S02  every single_skill / multi_skill item has a unique item_id
  S03  every q_vector has length == len(node_order) and is binary
  S04  every single-skill item's target_node is in node_order
  S05  every single-skill item's q_vector has a 1 at the target_node index
  S06  every multi-skill item has target_nodes of length >= 2, all in node_order
  S07  every multi-skill item's q_vector has a 1 at each target_nodes index
  S08  prerequisites (if present) only references skills in node_order
  S09  prerequisites (if present) has no cycles
  S10  short_labels (if present) only references skills in node_order
  S11  every skill is mentioned by at least one item's q_vector
       (warning, not error -- coverage gap)

Usage:
    python validate_bank.py path/to/bank.json
    -> exits 0 on pass, 1 on any error (warnings don't fail)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Each check appends to either errors (hard fail) or warnings (informational)
def validate(bank: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    meta = bank.get("meta", {})

    # --- S01: node_order is a list of unique non-empty strings ---
    nodes = meta.get("node_order")
    if not isinstance(nodes, list) or not nodes:
        errors.append("S01: meta.node_order missing or empty")
        return errors, warnings
    if any(not isinstance(n, str) or not n.strip() for n in nodes):
        errors.append("S01: meta.node_order has non-string or empty entries")
    if len(set(nodes)) != len(nodes):
        dups = [n for n in nodes if nodes.count(n) > 1]
        errors.append(f"S01: meta.node_order has duplicates: {sorted(set(dups))}")
    K = len(nodes)
    node_set = set(nodes)

    single_items = bank.get("single_skill_items", [])
    multi_items = bank.get("multi_skill_items", [])
    all_items = list(single_items) + list(multi_items)

    # --- S02: unique item_ids ---
    seen_ids: set[str] = set()
    for it in all_items:
        iid = it.get("item_id")
        if not iid:
            errors.append("S02: item missing item_id")
            continue
        if iid in seen_ids:
            errors.append(f"S02: duplicate item_id: {iid}")
        seen_ids.add(iid)

    # --- S03: q_vector shape and binary ---
    for it in all_items:
        iid = it.get("item_id", "<unknown>")
        q = it.get("q_vector")
        if not isinstance(q, list):
            errors.append(f"S03: {iid} has non-list q_vector")
            continue
        if len(q) != K:
            errors.append(
                f"S03: {iid} q_vector length {len(q)} != node_order length {K}"
            )
            continue
        if any(b not in (0, 1) for b in q):
            errors.append(f"S03: {iid} q_vector has non-binary entries")

    # --- S04 / S05: single-skill target_node and q_vector alignment ---
    for it in single_items:
        iid = it.get("item_id", "<unknown>")
        target = it.get("target_node")
        if target not in node_set:
            errors.append(f"S04: {iid} target_node {target!r} not in node_order")
            continue
        q = it.get("q_vector", [])
        if len(q) == K and q[nodes.index(target)] != 1:
            errors.append(
                f"S05: {iid} q_vector is 0 at its target_node {target!r}"
            )

    # --- S06 / S07: multi-skill target_nodes ---
    for it in multi_items:
        iid = it.get("item_id", "<unknown>")
        targets = it.get("target_nodes", [])
        if not isinstance(targets, list) or len(targets) < 2:
            errors.append(
                f"S06: {iid} target_nodes must be a list of length >= 2, "
                f"got {targets!r}"
            )
            continue
        missing = [t for t in targets if t not in node_set]
        if missing:
            errors.append(
                f"S06: {iid} target_nodes references unknown skills: {missing}"
            )
            continue
        q = it.get("q_vector", [])
        if len(q) == K:
            for t in targets:
                if q[nodes.index(t)] != 1:
                    errors.append(
                        f"S07: {iid} q_vector is 0 at target_node {t!r}"
                    )

    # --- S08 / S09: prerequisites references and acyclicity ---
    prereqs = meta.get("prerequisites")
    if prereqs is not None:
        if not isinstance(prereqs, dict):
            errors.append("S08: meta.prerequisites must be a dict")
        else:
            for child, parents in prereqs.items():
                if child not in node_set:
                    errors.append(
                        f"S08: prerequisites references unknown skill {child!r}"
                    )
                if not isinstance(parents, list):
                    errors.append(
                        f"S08: prerequisites[{child!r}] must be a list"
                    )
                    continue
                for p in parents:
                    if p not in node_set:
                        errors.append(
                            f"S08: prerequisites[{child!r}] references "
                            f"unknown skill {p!r}"
                        )
                if child in parents:
                    errors.append(
                        f"S09: prerequisites[{child!r}] contains itself"
                    )
            # Kahn-style topological sort to detect cycles
            indeg = {n: 0 for n in nodes}
            adj: dict[str, list[str]] = {n: [] for n in nodes}
            for child, parents in prereqs.items():
                if child not in node_set:
                    continue
                for p in parents:
                    if p in node_set:
                        adj[p].append(child)
                        indeg[child] += 1
            queue = [n for n in nodes if indeg[n] == 0]
            visited = 0
            while queue:
                n = queue.pop()
                visited += 1
                for m in adj[n]:
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        queue.append(m)
            if visited != len(nodes):
                remaining = [n for n in nodes if indeg[n] > 0]
                errors.append(
                    f"S09: cycle in prerequisites among: {remaining}"
                )

    # --- S10: short_labels references ---
    short_labels = meta.get("short_labels")
    if short_labels is not None:
        if not isinstance(short_labels, dict):
            errors.append("S10: meta.short_labels must be a dict")
        else:
            unknown = [k for k in short_labels if k not in node_set]
            if unknown:
                errors.append(
                    f"S10: short_labels references unknown skills: {unknown}"
                )

    # --- S11: every skill should be touched by at least one item ---
    skill_hit = [0] * K
    for it in all_items:
        q = it.get("q_vector", [])
        if len(q) == K:
            for i, b in enumerate(q):
                if b == 1:
                    skill_hit[i] += 1
    untouched = [nodes[i] for i, c in enumerate(skill_hit) if c == 0]
    if untouched:
        warnings.append(
            f"S11: no items mention these skills (no diagnostic signal): "
            f"{untouched}"
        )
    # Also warn about thin coverage on any skill
    thin = [(nodes[i], skill_hit[i]) for i in range(K) if 0 < skill_hit[i] < 2]
    if thin:
        warnings.append(
            f"S11: thin coverage (only 1 item touches): "
            f"{[t[0] for t in thin]}"
        )

    return errors, warnings


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bank_path", help="path to item bank JSON")
    args = ap.parse_args()

    bank = json.loads(Path(args.bank_path).read_text())
    errors, warnings = validate(bank)

    if warnings:
        print("warnings:")
        for w in warnings:
            print(f"  {w}")
    if errors:
        print("errors:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    print(f"OK: {args.bank_path}  "
          f"({len(bank['meta']['node_order'])} skills, "
          f"{len(bank.get('single_skill_items', []))} single-skill, "
          f"{len(bank.get('multi_skill_items', []))} multi-skill items)")
    sys.exit(0)


if __name__ == "__main__":
    _main()
