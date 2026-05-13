"""Agentic item bank generator.

Given a topic specification (subject, skill list with descriptions, the
prerequisite DAG, cultural context, and any design notes), the LLM uses
verification tools in a tool-use loop to build a complete item bank that
passes validate_bank.py:

  - coverage_report()  : how many items per skill so far, what's still needed
  - verify_item(item)  : runs structural checks + arithmetic verification
                         against the model's claimed solution_expression
  - add_item(item)     : commits to the bank after passing verify_item

The loop runs until the model either returns text without a tool call
(typically "DONE") or hits the iteration budget.

Solution verification uses safe AST-restricted evaluation of a Python
expression in the Fraction class, then compares against the marked
correct option (parsed as Fraction). This catches arithmetic errors
the model makes when constructing items -- the most common failure
mode of LLM-generated math content.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from llm_client import OllamaClient, Tool, AgentResult


# --------------------------------------------------------------------------
# Safe expression evaluation (used by verify_item)
# --------------------------------------------------------------------------

_SAFE_GLOBALS = {"Fraction": Fraction, "__builtins__": {}}
_ALLOWED_AST_NODES = (
    ast.Expression, ast.Constant, ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
    ast.Pow, ast.Mod, ast.USub, ast.UAdd,
    ast.Call, ast.Name, ast.Load,
)


def safe_eval_fraction(expr: str) -> Fraction:
    """Evaluate a Python expression in a restricted environment containing
    only the Fraction class and arithmetic operators. Anything else raises.
    """
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id != "Fraction":
            raise ValueError(f"only Fraction is allowed; got {node.id!r}")
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise ValueError(f"unsafe ast node: {type(node).__name__}")
    result = eval(expr, _SAFE_GLOBALS, {})
    if not isinstance(result, (int, Fraction,float)):
        raise ValueError(f"expression must evaluate to a number, got {type(result).__name__}")
    return Fraction(result).limit_denominator(1000)


def parse_option_to_fraction(s: str) -> Fraction:
    """Parse '1/2', '3', '2 1/2', '5/2', or '1\u00bd' to Fraction.

    Unicode vulgar fractions are recognised in the standard set. Also
    auto-repairs the common UTF-8-misread-as-Latin-1 mojibake: when the
    model emits '\u00be' (UTF-8 bytes 0xC2 0xBE) and the bytes get
    misdecoded as Latin-1, the string arrives as '\u00c2\u00be'. The
    telltale '\u00c2' or '\u00e2' lead-byte character at start of a
    multi-char sequence is detected and the string is re-encoded.
    """
    s = s.strip()

    # Auto-repair mojibake before any other processing
    if "\u00c2" in s or "\u00e2" in s:
        try:
            s = s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    # Map common unicode vulgar fractions to (num, den)
    vulg = {
        "\u00bd": (1, 2),  "\u2153": (1, 3),  "\u2154": (2, 3),
        "\u00bc": (1, 4),  "\u00be": (3, 4),  "\u2155": (1, 5),
        "\u2156": (2, 5),  "\u2157": (3, 5),  "\u2158": (4, 5),
        "\u2159": (1, 6),  "\u215a": (5, 6),  "\u2150": (1, 7),
        "\u215b": (1, 8),  "\u215c": (3, 8),  "\u215d": (5, 8),
        "\u215e": (7, 8),
    }
    # Replace any unicode vulgar with " n/d" so mixed parsing works
    for ch, (n, d) in vulg.items():
        if ch in s:
            s = s.replace(ch, f" {n}/{d}")
    s = s.strip()
    parts = s.split()
    if len(parts) == 2:                     # mixed number "2 1/2"
        whole = int(parts[0])
        n, d = parts[1].split("/")
        sign = -1 if whole < 0 else 1
        return Fraction(whole) + sign * Fraction(int(n), int(d))
    if "/" in s:
        n, d = s.split("/")
        return Fraction(int(n), int(d))
    if "." in s:
        return Fraction(s).limit_denominator(1000)
    return Fraction(int(s))


# --------------------------------------------------------------------------
# Item schema (the model passes one of these to add_item)
# --------------------------------------------------------------------------

class GeneratedItem(BaseModel):
    """The structure the LLM must produce when calling add_item."""
    stem: str = Field(description="The question text, in context")
    options: dict[str, str] = Field(
        description="Multiple-choice options keyed exactly A, B, C, D"
    )
    correct_option: str = Field(
        description="Letter of the correct option, one of A B C D"
    )
    solution_expression: str = Field(
        description=(
            "A Python expression that evaluates to the correct answer "
            "using ONLY the Fraction class and arithmetic operators. "
            "Fraction takes EXACTLY 2 args; it does NOT parse mixed-number "
            "strings. Convert mixed numbers to improper fractions FIRST: "
            "for an answer of 2 1/3, write Fraction(7, 3) -- not "
            "Fraction(2, 1, 3) (invalid) and not Fraction('2 1/3') (invalid). "
            "Examples: 'Fraction(2,3) * Fraction(3,4)', 'Fraction(7,3)', "
            "'Fraction(11,4) / Fraction(3,2)'."
        )
    )
    distractor_rationales: dict[str, str] = Field(
        description=(
            "Map each WRONG option letter (not the correct one) to a "
            "short misconception name like 'adds_numerators_and_denominators' "
            "or 'forgets_to_simplify'."
        )
    )
    q_vector: list[int] = Field(
        description=(
            "Binary list aligned with the bank's node_order. 1 if the "
            "skill is strictly required to solve this item, else 0. "
            "Minimum-cognitive convention: only mark skills the item "
            "directly exercises, not all DAG ancestors."
        )
    )
    target_node: str | None = Field(
        default=None,
        description=(
            "For a single-skill item, the one skill being tested. "
            "Mutually exclusive with target_nodes."
        ),
    )
    target_nodes: list[str] | None = Field(
        default=None,
        description=(
            "For a multi-skill item, the 2-4 skills being chained. "
            "Mutually exclusive with target_node."
        ),
    )
    difficulty_hint: str = Field(
        description="One of: easy, medium, hard",
    )


# --------------------------------------------------------------------------
# Bank state, tools, and verification
# --------------------------------------------------------------------------

class BankState:
    """Tracks items committed so far + provides verification logic."""

    def __init__(self, spec: dict, target_per_skill: int, target_multi: int):
        self.spec = spec
        self.target_per_skill = target_per_skill
        self.target_multi = target_multi
        self.nodes: list[str] = spec["node_order"]
        self.node_set = set(self.nodes)

        self.single_items: list[dict] = []
        self.multi_items: list[dict] = []
        self._id_counters: dict[str, int] = defaultdict(int)

    # ----- coverage --------------------------------------------------------

    def coverage(self) -> dict:
        """Per-skill count of pure items + total multi-skill count."""
        pure_per_skill = {n: 0 for n in self.nodes}
        multi_per_skill = {n: 0 for n in self.nodes}
        for it in self.single_items:
            t = it.get("target_node")
            if t in pure_per_skill:
                pure_per_skill[t] += 1
        for it in self.multi_items:
            for t in it.get("target_nodes", []):
                if t in multi_per_skill:
                    multi_per_skill[t] += 1

        needed_pure = {
            n: max(0, self.target_per_skill - pure_per_skill[n])
            for n in self.nodes
        }
        needed_multi = max(0, self.target_multi - len(self.multi_items))
        return {
            "pure_per_skill": pure_per_skill,
            "multi_per_skill": multi_per_skill,
            "needed_pure": needed_pure,
            "needed_multi_total": needed_multi,
            "total_single_items": len(self.single_items),
            "total_multi_items": len(self.multi_items),
            "complete": all(v == 0 for v in needed_pure.values()) and needed_multi == 0,
        }

    # ----- verification ----------------------------------------------------

    def verify_item(self, item_dict: dict) -> dict:
        """Run all checks on a candidate item. Returns dict with `ok` bool
        and `errors` list. Performs structural checks first, then runs the
        arithmetic verification via safe_eval_fraction."""
        errors: list[str] = []

        # Parse with Pydantic for structural validation
        try:
            item = GeneratedItem.model_validate(item_dict)
        except Exception as e:
            return {"ok": False, "errors": [f"schema_error: {e}"]}

        # --- options must be A B C D ---
        if set(item.options.keys()) != {"A", "B", "C", "D"}:
            errors.append(
                f"options_keys: must be exactly A B C D, got {sorted(item.options.keys())}"
            )

        # --- correct_option must be one of A B C D ---
        if item.correct_option not in ("A", "B", "C", "D"):
            errors.append(
                f"correct_option: must be A B C D, got {item.correct_option!r}"
            )

        # --- distractor_rationales covers wrong options only ---
        expected_distractors = {"A", "B", "C", "D"} - {item.correct_option}
        if set(item.distractor_rationales.keys()) != expected_distractors:
            errors.append(
                f"distractor_rationales: expected keys {sorted(expected_distractors)}, "
                f"got {sorted(item.distractor_rationales.keys())}"
            )

        # --- exactly one of target_node / target_nodes ---
        has_single = item.target_node is not None
        has_multi = item.target_nodes is not None and len(item.target_nodes) > 0
        if has_single == has_multi:
            errors.append(
                "target: must set exactly one of target_node OR target_nodes"
            )
        if has_single and item.target_node not in self.node_set:
            errors.append(f"target_node: {item.target_node!r} not in node_order")
        if has_multi:
            if len(item.target_nodes) < 2:
                errors.append("target_nodes: must have >= 2 entries for multi-skill")
            bad = [n for n in item.target_nodes if n not in self.node_set]
            if bad:
                errors.append(f"target_nodes: unknown skills {bad}")

        # --- q_vector length and binary ---
        if len(item.q_vector) != len(self.nodes):
            errors.append(
                f"q_vector: length {len(item.q_vector)} != node_order length {len(self.nodes)}"
            )
        elif any(b not in (0, 1) for b in item.q_vector):
            errors.append("q_vector: must be binary (0 or 1 only)")
        else:
            # target(s) must be marked 1 in the q_vector
            if has_single:
                idx = self.nodes.index(item.target_node)
                if item.q_vector[idx] != 1:
                    errors.append(
                        f"q_vector: target_node {item.target_node!r} must be 1"
                    )
            if has_multi:
                for t in item.target_nodes:
                    idx = self.nodes.index(t)
                    if item.q_vector[idx] != 1:
                        errors.append(
                            f"q_vector: target_node {t!r} must be 1"
                        )

        # --- difficulty hint ---
        if item.difficulty_hint not in ("easy", "medium", "hard"):
            errors.append(
                f"difficulty_hint: must be easy/medium/hard, got "
                f"{item.difficulty_hint!r}"
            )

        # --- arithmetic verification (the agentic value-add) ---
        try:
            computed = safe_eval_fraction(item.solution_expression)
        except Exception as e:
            errors.append(f"solution_expression_invalid: {e}")
            computed = None

        if computed is not None and item.correct_option in item.options:
            try:
                marked_value = parse_option_to_fraction(item.options[item.correct_option])
            except Exception as e:
                errors.append(
                    f"correct_option_unparseable: cannot parse "
                    f"{item.options[item.correct_option]!r}: {e}"
                )
                marked_value = None
            if marked_value is not None and computed != marked_value:
                errors.append(
                    f"arithmetic_mismatch: solution_expression evaluates to "
                    f"{computed} but the marked correct option "
                    f"{item.correct_option}='{item.options[item.correct_option]}' "
                    f"parses to {marked_value}. Either the expression is wrong, "
                    f"the marked option is wrong, or you marked the wrong letter."
                )

            # Also: no two options should parse to the same value
            parsed_options = {}
            for letter, val in item.options.items():
                try:
                    parsed_options[letter] = parse_option_to_fraction(val)
                except Exception:
                    pass
            if len(parsed_options) > 1:
                seen = {}
                for letter, val in parsed_options.items():
                    if val in seen:
                        errors.append(
                            f"duplicate_options: {seen[val]}={item.options[seen[val]]} "
                            f"and {letter}={item.options[letter]} both equal {val}"
                        )
                        break
                    seen[val] = letter

        return {"ok": not errors, "errors": errors}

    # ----- commit ----------------------------------------------------------

    def add_item(self, item_dict: dict) -> dict:
        """Verify then commit. Returns success or detailed errors."""
        v = self.verify_item(item_dict)
        if not v["ok"]:
            return {"ok": False, "errors": v["errors"]}

        item = GeneratedItem.model_validate(item_dict)

        # Auto-generate item_id
        if item.target_node is not None:
            prefix = _id_prefix_for(item.target_node)
            self._id_counters[prefix] += 1
            item_id = f"{prefix}_{self._id_counters[prefix]:03d}"
            record = item.model_dump()
            record["item_id"] = item_id
            self.single_items.append(record)
        else:
            self._id_counters["MULTI"] += 1
            item_id = f"MULTI_{self._id_counters['MULTI']:03d}"
            record = item.model_dump()
            record["item_id"] = item_id
            self.multi_items.append(record)

        return {"ok": True, "item_id": item_id,
                "coverage": self.coverage()}

    # ----- finalise --------------------------------------------------------

    def to_bank_json(self) -> dict:
        return {
            "meta": {
                "subject": self.spec.get("subject", ""),
                "curriculum_context": self.spec.get("curriculum_context", ""),
                "node_order": self.nodes,
                "prerequisites": self.spec.get("prerequisites", {}),
                "short_labels": self.spec.get("short_labels", {}),
                "q_vector_convention": (
                    "Minimum-cognitive: only skills strictly needed are marked. "
                    "Bank generated by bank_generator.py."
                ),
                "design_notes": self.spec.get("design_notes", []),
            },
            "single_skill_items": self.single_items,
            "multi_skill_items": self.multi_items,
        }


def _id_prefix_for(skill: str) -> str:
    """Auto-generate an item-id prefix from a skill name.
    Multiplication -> MULT, Concept_of_fraction -> COF, Multiplying_fractions -> MUF.
    """
    parts = re.split(r"[_\s]+", skill)
    if len(parts) >= 3:
        return (parts[0][0] + parts[1][0] + parts[2][0]).upper()
    if len(parts) == 2:
        return (parts[0][:2] + parts[1][:1]).upper()
    return parts[0][:4].upper()


# --------------------------------------------------------------------------
# Tool factory: turns BankState methods into Tool objects
# --------------------------------------------------------------------------

def build_tools(state: BankState) -> list[Tool]:
    return [
        Tool(
            name="coverage_report",
            description=(
                "Report what items have been added so far and what is still "
                "needed to reach the target counts. Call this whenever you "
                "want to decide what to generate next."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: state.coverage(),
        ),
        Tool(
            name="verify_item",
            description=(
                "Run structural checks AND arithmetic verification on a "
                "candidate item without committing it. Returns ok=true if "
                "the item would be accepted, or a list of specific errors. "
                "Use this to test an item before calling add_item."
            ),
            parameters={
                "type": "object",
                "properties": _item_json_schema(state.nodes),
                "required": ["stem", "options", "correct_option",
                             "solution_expression", "distractor_rationales",
                             "q_vector", "difficulty_hint"],
            },
            handler=lambda args: state.verify_item(args),
        ),
        Tool(
            name="add_item",
            description=(
                "Commit an item to the bank. Internally runs the same checks "
                "as verify_item; on failure, returns errors and does NOT add "
                "the item. On success, returns the assigned item_id and the "
                "updated coverage report."
            ),
            parameters={
                "type": "object",
                "properties": _item_json_schema(state.nodes),
                "required": ["stem", "options", "correct_option",
                             "solution_expression", "distractor_rationales",
                             "q_vector", "difficulty_hint"],
            },
            handler=lambda args: state.add_item(args),
        ),
    ]


def _item_json_schema(nodes: list[str]) -> dict:
    """Build the JSON schema fragment for an item, for tool parameter docs."""
    return {
        "stem": {"type": "string"},
        "options": {
            "type": "object",
            "description": "Map A,B,C,D to option text strings",
        },
        "correct_option": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "solution_expression": {
            "type": "string",
            "description": (
                "Python expression using ONLY Fraction(numerator, denominator) "
                "and +, -, *, /, **. Fraction takes EXACTLY 2 args. For mixed "
                "numbers, convert to improper FIRST: 2 1/3 -> Fraction(7, 3). "
                "Example: 'Fraction(2,3) * Fraction(3,4)'."
            ),
        },
        "distractor_rationales": {
            "type": "object",
            "description": "Map each wrong option letter to misconception name",
        },
        "q_vector": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 1},
            "description": f"Binary list of length {len(nodes)} aligned with node_order",
        },
        "target_node": {"type": "string",
                        "description": "For single-skill items; one of: " +
                        ", ".join(nodes)},
        "target_nodes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "For multi-skill items: 2-4 skills",
        },
        "difficulty_hint": {"type": "string", "enum": ["easy", "medium", "hard"]},
    }


# --------------------------------------------------------------------------
# Initial prompt
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert mathematics item-bank author. You build a
diagnostic item bank by calling the provided TOOLS, NOT by writing prose.

YOUR FIRST ACTION must be to call the `coverage_report` tool. Do not write any
preface or commentary before this. After seeing the coverage report, call
`add_item` to commit each item one at a time. NEVER produce free-form text
unless and until the bank is complete (then reply with the single word DONE).

If you find yourself wanting to write an item as a markdown block (stem on one
line, options on the next, "Solution:" header, etc.) -- STOP. That belongs in
an add_item call, not in your message. Convert it to the add_item schema
(stem, options, correct_option, solution_expression, distractor_rationales,
q_vector, target_node, difficulty_hint) and submit via the tool.

The arithmetic verifier runs every item you submit: it evaluates your
`solution_expression` and compares to the marked correct option. A mismatch
means your math is wrong or you marked the wrong letter. Before submitting,
mentally:
  1. Compute the answer step-by-step.
  2. Place it as ONE of the options.
  3. Make sure no other option happens to equal the same value (duplicate_options).
  4. Set correct_option to the letter where you placed the answer.

MIXED NUMBERS -- READ CAREFULLY, this is the most common failure point:
- A mixed number "2 1/3" means (2 + 1/3) = 7/3 as an improper fraction.
- In an option string, write a mixed number with a single space: "2 1/3"
  (NOT "21/3", NOT "2-1/3", NOT "2 and 1/3").
- In solution_expression, the Fraction class takes EXACTLY 2 arguments and
  does NOT parse mixed-number strings. You MUST convert to improper first:
      mixed 1 1/2   ->  Fraction(3, 2)
      mixed 2 1/3   ->  Fraction(7, 3)
      mixed 2 3/4   ->  Fraction(11, 4)
      mixed 5 1/8   ->  Fraction(41, 8)
  General rule: a + b/c becomes Fraction(a*c + b, c).
- These are INVALID and will be rejected:
      Fraction(2, 1, 3)        (too many args)
      Fraction("2 1/3")        (Fraction does not parse strings like that)
      Fraction(2) + 1/3        (Python evaluates 1/3 as 0.333... float)
  Use Fraction(7, 3) or Fraction(2) + Fraction(1, 3) instead.
- The option "7/3" and the option "2 1/3" parse to the same value. Never
  put both in the same item (that triggers duplicate_options).

When add_item returns an error, READ the error carefully and FIX ONLY the
specific issue named. Do not rewrite the entire item from scratch. Do not
give up and produce prose -- always retry through the tool.

Keep stems short, age-appropriate, and culturally grounded per the cultural
context. Distractors must each correspond to a SPECIFIC named misconception.
Use the minimum-cognitive q_vector convention: only mark skills the item
DIRECTLY exercises, not all DAG ancestors.

Before calling add_item, you may optionally call verify_item with the same
payload -- it runs the same checks but does not commit. Use it for items
where you are unsure about the arithmetic, especially when the answer is
a mixed number or an improper fraction."""


def _build_initial_prompt(spec: dict, target_per_skill: int,
                          target_multi: int) -> str:
    skills = spec["node_order"]
    descs = spec.get("skill_descriptions", {})
    prereqs = spec.get("prerequisites", {})
    skill_lines = []
    for s in skills:
        d = descs.get(s, "(no description)")
        skill_lines.append(f"  - {s}: {d}")
    prereq_lines = []
    for child, parents in prereqs.items():
        if parents:
            prereq_lines.append(f"  - {child} requires {', '.join(parents)}")

    notes = spec.get("design_notes", [])
    notes_text = "\n".join(f"  - {n}" for n in notes) if notes else "  (none)"

    return (
        f"Subject: {spec.get('subject', '(unspecified)')}\n"
        f"Curriculum context: {spec.get('curriculum_context', '')}\n"
        f"Cultural context: {spec.get('cultural_context', '')}\n\n"
        f"Skills (in topological order, aligned with q_vector):\n"
        + "\n".join(skill_lines)
        + "\n\nPrerequisites (DAG):\n"
        + ("\n".join(prereq_lines) if prereq_lines else "  (none)")
        + f"\n\nDesign notes:\n{notes_text}\n\n"
        + f"Targets:\n"
        + f"  - {target_per_skill} pure single-skill item(s) per skill\n"
        + f"  - {target_multi} multi-skill items chaining 2-4 skills\n\n"
        + "Procedure:\n"
        + "  1. Call coverage_report to see what's needed.\n"
        + "  2. Generate ONE item at a time, passing it to add_item.\n"
        + "  3. If add_item returns errors, fix them and retry that item.\n"
        + "  4. Repeat until coverage_report shows complete=true.\n"
        + "  5. When complete, reply with the single word DONE.\n\n"
        + "Begin."
    )


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def generate_bank(
    spec: dict,
    output_json: str | Path,
    *,
    client=None,
    target_per_skill: int = 2,
    target_multi: int = 6,
    max_iterations: int = 200,
    verbose: bool = True,
    thinking: bool | None = None,
) -> tuple[dict, AgentResult]:
    """Run the agentic loop until the bank is complete (or budget hit).

    `thinking=None` uses the client default. Pass `thinking=False` to
    disable Gemma 4 thinking mode for the agent loop -- substantially
    faster per iteration on weak hardware. Pass True to force it on.
    """
    if client is None:
        client = OllamaClient()

    state = BankState(spec, target_per_skill, target_multi)
    tools = build_tools(state)
    prompt = _build_initial_prompt(spec, target_per_skill, target_multi)

    counters = {"add_ok": 0, "add_fail": 0, "verify": 0, "coverage": 0}

    def on_call(name, args, result):
        if name == "coverage_report":
            counters["coverage"] += 1
        elif name == "verify_item":
            counters["verify"] += 1
        elif name == "add_item":
            if result.get("ok"):
                counters["add_ok"] += 1
                if verbose:
                    print(f"  + {result['item_id']}  "
                          f"({result['coverage']['total_single_items']} single, "
                          f"{result['coverage']['total_multi_items']} multi)")
            else:
                counters["add_fail"] += 1
                if verbose:
                    print(f"  ! add_item failed: "
                          f"{'; '.join(result.get('errors', [])[:2])}")

    def make_continuation_check():
        """Refuse exit if coverage is incomplete. Tells the model EXACTLY
        what's still missing so it can resume effectively. Two safety
        valves: a hard cap on total nudges, and stuck-detection that
        gives up once the model has stopped making progress between
        nudges (model is in a degenerate state, no point pushing further).
        Also detects the prose-emission failure mode (model writes an
        item as markdown instead of calling add_item) and pushes back
        with a schema reminder.
        """
        nudges = {"n": 0, "max": 20}
        last_count = {"v": 0}

        def looks_like_prose_item(text: str) -> bool:
            """Heuristic for 'model wrote an item as text instead of
            calling add_item'. Signals: markdown headers, option markers,
            'Solution:' / 'Stem:' / 'Question:' labels."""
            t = (text or "").lower().strip()
            if not t or t == "done":
                return False
            signals = [
                "**stem", "**options", "**solution", "**answer",
                "**concept", "**question", "**problem",
                "stem:", "options:", "solution:", "answer:",
                "question:", "problem:",
                "a) ", "b) ", "c) ", "d) ",
            ]
            return any(s in t for s in signals)

        def check(final_text: str) -> str | None:
            cov = state.coverage()
            if cov["complete"]:
                return None
            current_count = cov["total_single_items"] + cov["total_multi_items"]
            if nudges["n"] >= nudges["max"]:
                if verbose:
                    print(f"  [continuation] hit nudge cap ({nudges['max']}); "
                          f"accepting partial bank", flush=True)
                return None
            if nudges["n"] > 0 and current_count == last_count["v"]:
                if verbose:
                    print(f"  [continuation] no progress since last nudge "
                          f"({current_count} items); accepting partial bank",
                          flush=True)
                return None
            last_count["v"] = current_count
            nudges["n"] += 1
            missing_pure = sorted(
                s for s, n in cov["needed_pure"].items() if n > 0
            )

            # Detect prose-form items and address them specifically
            if looks_like_prose_item(final_text):
                return (
                    "STOP -- you wrote that item as plain text/markdown. "
                    "It MUST be submitted via the add_item tool. Take the "
                    "item you just wrote, convert it to the add_item schema "
                    "(stem, options A/B/C/D, correct_option, solution_expression "
                    "using Fraction(), distractor_rationales for the wrong "
                    "letters only, q_vector binary list of length "
                    f"{len(state.nodes)}, target_node OR target_nodes, "
                    "difficulty_hint), and CALL add_item. Coverage status: "
                    f"{cov['total_single_items']} single, "
                    f"{cov['total_multi_items']} multi items committed. "
                    f"Skills still needing pure items: {missing_pure}."
                )

            # Default: generic coverage-incomplete nudge
            return (
                f"The bank is NOT yet complete. Coverage right now: "
                f"{cov['total_single_items']} single-skill items, "
                f"{cov['total_multi_items']} multi-skill items. "
                f"Still needed: pure items for skills {missing_pure}; "
                f"{cov['needed_multi_total']} more multi-skill item(s). "
                f"Resume calling add_item. Do NOT reply with DONE or any "
                f"other text until coverage_report returns complete=true."
            )
        return check

    result = client.agent_loop(
        prompt, tools, system=SYSTEM_PROMPT,
        max_iterations=max_iterations,
        on_tool_call=on_call,
        verbose=verbose,
        tool_temperature=0.15,
        continuation_check=make_continuation_check(),
        thinking=thinking,
    )

    bank = state.to_bank_json()
    out = Path(output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bank, indent=2, ensure_ascii=False))

    if verbose:
        print(f"\ngenerated {len(state.single_items)} single + "
              f"{len(state.multi_items)} multi items in {result.iterations} iterations")
        print(f"tool calls: coverage_report={counters['coverage']}, "
              f"verify_item={counters['verify']}, "
              f"add_item ok={counters['add_ok']} fail={counters['add_fail']}")
        if (counters['add_ok'] == 0 and counters['coverage'] == 0
                and counters['verify'] == 0):
            print("\nERROR: model exited without calling ANY tool. "
                  "Its final message was:")
            print("---")
            print(result.final_message or "(empty)")
            print("---")
            print("Likely causes: (a) tool calling not engaging with this "
                  "model build, (b) prompt let the model preface chattily, "
                  "or (c) Ollama version too old for tools. Run the "
                  "tool-calling smoke test to isolate.")
        elif not result.finished_cleanly:
            print(f"WARNING: agent loop hit iteration budget ({max_iterations}); "
                  f"bank may be incomplete")
        print(f"wrote {out}")

    return bank, result


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True,
                    help="path to topic spec JSON")
    ap.add_argument("--output", required=True,
                    help="path to write generated bank JSON")
    ap.add_argument("--target-per-skill", type=int, default=2)
    ap.add_argument("--target-multi", type=int, default=6)
    ap.add_argument("--max-iterations", type=int, default=200)
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default=None)
    thinking_group = ap.add_mutually_exclusive_group()
    thinking_group.add_argument(
        "--no-thinking", dest="thinking", action="store_false", default=None,
        help="disable Gemma 4 thinking mode for the agent loop (faster, possibly less accurate math)",
    )
    thinking_group.add_argument(
        "--thinking", dest="thinking", action="store_true", default=None,
        help="force thinking mode ON for the agent loop",
    )
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text())

    client_kwargs = {}
    if args.model:  client_kwargs["model"] = args.model
    if args.host:   client_kwargs["host"] = args.host
    client = OllamaClient(**client_kwargs)

    generate_bank(
        spec, args.output, client=client,
        target_per_skill=args.target_per_skill,
        target_multi=args.target_multi,
        max_iterations=args.max_iterations,
        thinking=args.thinking,
    )


if __name__ == "__main__":
    _main()
