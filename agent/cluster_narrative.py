"""Cluster (and optional per-student) narratives via local LLM.

Takes cluster_summary.xlsx + bank.json and generates a 100-150 word
plain-English narrative for each cluster covering:
  - what students in this cluster likely understand
  - what they probably misunderstand
  - what a teacher should watch for during remediation

Writes the enhanced summary back as cluster_summary_with_narratives.xlsx.

Optionally also generates per-student narratives from mastery_with_clusters.xlsx
-- useful for parent-teacher reports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from llm_client import OllamaClient, MockClient


# ---- output schemas -------------------------------------------------------

class ClusterNarrative(BaseModel):
    likely_understands: str = Field(
        description="2-3 sentences on what students in this cluster have likely mastered"
    )
    likely_misunderstands: str = Field(
        description="2-3 sentences on the conceptual gaps or misconceptions implied by the mastery profile"
    )
    teaching_watchpoints: str = Field(
        description="2-3 sentences on what a teacher should pay attention to when remediating"
    )


class StudentNarrative(BaseModel):
    summary: str = Field(
        description="2-3 sentences on the student's overall standing and next focus areas"
    )


# ---- prompt construction --------------------------------------------------

SYSTEM_PROMPT = """You are a math-education researcher writing diagnostic summaries
for a Ghanaian classroom. Be specific, concrete, and brief. Refer to skills by
their exact names. Do not invent skills not in the provided list. Do not include
generic teaching advice; ground every observation in the mastery data."""


def _cluster_prompt(row: pd.Series, skills: list[str], subject: str,
                    prereqs: dict) -> str:
    centroid_lines = []
    for s in skills:
        v = row.get(f"centroid_{s}")
        if pd.notna(v):
            band = "mastered" if v >= 1.0 else "partial" if v >= 0.5 else "not mastered"
            centroid_lines.append(f"  {s}: {v:.2f} ({band})")

    prereq_lines = []
    for child, parents in prereqs.items():
        if parents:
            prereq_lines.append(f"  {child} requires: {', '.join(parents)}")

    return (
        f"Subject: {subject}\n"
        f"Cluster: {row['cluster']}  size: {row['size']} students  "
        f"mean mastery: {row['mean_mastery']:.2f}  label: {row['label']}\n\n"
        f"Per-skill mastery (0=not, 0.5=partial, 1=mastered):\n"
        + "\n".join(centroid_lines)
        + "\n\nSkill prerequisites:\n"
        + ("\n".join(prereq_lines) if prereq_lines else "  (none specified)")
        + "\n\nWrite a diagnostic narrative for this cluster, returning JSON with "
        + "exactly these three fields: likely_understands, likely_misunderstands, "
        + "teaching_watchpoints. Each field should be 2-3 sentences."
    )


def _student_prompt(row: pd.Series, skills: list[str], subject: str) -> str:
    mastery_lines = []
    for s in skills:
        v = row.get(s)
        if pd.notna(v):
            band = "mastered" if v >= 1.0 else "partial" if v >= 0.5 else "not mastered"
            mastery_lines.append(f"  {s}: {v:.2f} ({band})")
    return (
        f"Subject: {subject}\n"
        f"Student: {row['student_name']}  cluster: {row['cluster']}\n\n"
        f"Per-skill mastery:\n" + "\n".join(mastery_lines)
        + "\n\nWrite a brief 2-3 sentence narrative summarising the student's "
        + "standing and next focus areas. Return JSON with a single field `summary`."
    )


# ---- main entry points ----------------------------------------------------

def generate_cluster_narratives(
    cluster_summary_xlsx: str | Path,
    item_bank_json: str | Path,
    output_xlsx: str | Path,
    *,
    client=None,
    progress: bool = True,
) -> pd.DataFrame:
    """Add a `narrative` column (concatenated 3-field text) to the cluster
    summary and write the result to output_xlsx. Returns the DataFrame."""
    summary = pd.read_excel(cluster_summary_xlsx)
    bank = json.loads(Path(item_bank_json).read_text())
    skills = bank["meta"]["node_order"]
    subject = bank["meta"].get("subject", "Mathematics")
    prereqs = bank["meta"].get("prerequisites", {})

    if client is None:
        client = OllamaClient()

    narratives = []
    likely_und = []
    likely_mis = []
    watchpoints = []

    for _, row in summary.iterrows():
        prompt = _cluster_prompt(row, skills, subject, prereqs)
        if progress:
            print(f"  cluster {row['cluster']}: generating narrative...",
                  end="", flush=True)
        result = client.generate_json(
            prompt, schema=ClusterNarrative, system=SYSTEM_PROMPT,
        )
        full = (
            f"WHAT THEY LIKELY UNDERSTAND: {result.likely_understands}\n\n"
            f"WHAT THEY LIKELY MISUNDERSTAND: {result.likely_misunderstands}\n\n"
            f"TEACHING WATCHPOINTS: {result.teaching_watchpoints}"
        )
        narratives.append(full)
        likely_und.append(result.likely_understands)
        likely_mis.append(result.likely_misunderstands)
        watchpoints.append(result.teaching_watchpoints)
        if progress:
            print(" done.")

    summary["narrative"] = narratives
    summary["likely_understands"] = likely_und
    summary["likely_misunderstands"] = likely_mis
    summary["teaching_watchpoints"] = watchpoints

    out = Path(output_xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    summary.to_excel(out, index=False)
    print(f"\nwrote {out}")
    return summary


def generate_student_narratives(
    mastery_clustered_xlsx: str | Path,
    item_bank_json: str | Path,
    output_xlsx: str | Path,
    *,
    client=None,
    only_clusters: list[int] | None = None,
    progress: bool = True,
) -> pd.DataFrame:
    """Per-student narratives. Slower, run only when needed."""
    mastery = pd.read_excel(mastery_clustered_xlsx)
    bank = json.loads(Path(item_bank_json).read_text())
    skills = bank["meta"]["node_order"]
    subject = bank["meta"].get("subject", "Mathematics")

    if client is None:
        client = OllamaClient()

    summaries = []
    for i, (_, row) in enumerate(mastery.iterrows()):
        if row["cluster"] < 0:
            summaries.append("")  # unscored
            continue
        if only_clusters is not None and row["cluster"] not in only_clusters:
            summaries.append("")
            continue
        if progress and i % 10 == 0:
            print(f"  student {i+1}/{len(mastery)}: {row['student_name']}")
        result = client.generate_json(
            _student_prompt(row, skills, subject),
            schema=StudentNarrative,
            system=SYSTEM_PROMPT,
        )
        summaries.append(result.summary)

    mastery["narrative"] = summaries
    out = Path(output_xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    mastery.to_excel(out, index=False)
    print(f"\nwrote {out}")
    return mastery


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cluster-summary", required=True)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--output", required=True,
                    help="path to write cluster_summary_with_narratives.xlsx")
    ap.add_argument("--mastery-clustered", default=None,
                    help="if set, also generate per-student narratives")
    ap.add_argument("--student-output", default=None,
                    help="output path for per-student narratives")
    ap.add_argument("--only-clusters", default=None,
                    help="comma-separated cluster ids for per-student (default all)")
    ap.add_argument("--model", default=None, help="ollama model name")
    ap.add_argument("--host", default=None, help="ollama host URL")
    args = ap.parse_args()

    client_kwargs = {}
    if args.model:
        client_kwargs["model"] = args.model
    if args.host:
        client_kwargs["host"] = args.host
    client = OllamaClient(**client_kwargs)

    generate_cluster_narratives(
        args.cluster_summary, args.bank, args.output, client=client,
    )

    if args.mastery_clustered:
        if not args.student_output:
            raise SystemExit("--student-output is required with --mastery-clustered")
        only = ([int(x) for x in args.only_clusters.split(",")]
                if args.only_clusters else None)
        generate_student_narratives(
            args.mastery_clustered, args.bank, args.student_output,
            client=client, only_clusters=only,
        )


if __name__ == "__main__":
    _main()
