"""Remediation content generation via local LLM.

Takes cluster_paths.xlsx + cluster_summary.xlsx + bank.json and, for each
(cluster, skill) step in the path, generates a teaching micro-pack
containing:

  - explanation       : ~100-word in-context explanation of the skill
  - worked_example    : problem statement + step-by-step solution
  - practice_items    : 3 multiple-choice items with hints
  - common_mistakes   : 2-3 named misconceptions with corrections

The result is a structured JSON `remediation_pack.json`. Checkpoints
after every LLM call, so a crash mid-run only loses the in-flight step;
re-run with --resume to pick up where you left off. Optionally also
emits a printable Markdown file per cluster (--markdown-dir) and/or a
single combined PDF (--pdf).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from llm_client import OllamaClient
from remediation_to_pdf import build_remediation_pdf


# ---- output schemas -------------------------------------------------------

class WorkedExample(BaseModel):
    problem: str = Field(description="The problem statement, in context")
    solution_steps: list[str] = Field(
        description="Sequential solution steps, each one sentence"
    )
    final_answer: str = Field(description="The final answer, plainly stated")


class PracticeItem(BaseModel):
    stem: str = Field(description="The question text")
    options: dict[str, str] = Field(
        description="Multiple-choice options keyed A, B, C, D"
    )
    correct_option: str = Field(description="The letter of the correct option")
    hint: str = Field(
        description="One-line hint a teacher could give if a student is stuck"
    )


class CommonMistake(BaseModel):
    name: str = Field(description="Short name for the misconception")
    description: str = Field(
        description="One sentence describing what students do wrong"
    )
    correction: str = Field(
        description="One sentence on how to correct the thinking"
    )


class SkillPack(BaseModel):
    skill: str
    explanation: str = Field(
        description="100-150 word in-context explanation of the skill"
    )
    worked_example: WorkedExample
    practice_items: list[PracticeItem] = Field(
        description="Exactly 3 practice items with answers and hints",
        min_length=3, max_length=3,
    )
    common_mistakes: list[CommonMistake] = Field(
        description="2-3 named misconceptions students commonly make",
        min_length=2, max_length=3,
    )


# ---- prompt construction --------------------------------------------------

SYSTEM_PROMPT = """You are an experienced Ghanaian Junior High mathematics teacher
writing remediation materials. Use Ghana cedis (GH\u20b5), Ghanaian names (Kofi,
Ama, Akua, Kwame, Yaa, Esi), and local contexts (market, jollof, kelewele,
plantain) where appropriate. Be concrete, age-appropriate, and brief.

For practice items, use realistic small numbers. Make distractors plausible -
each wrong answer should correspond to a specific likely error. Return JSON
matching the requested schema exactly.

FORMATTING RULES (strict):
- Do NOT use LaTeX. Never write \\frac, \\div, \\times, \\cdot, or any
  backslash-commands. Never wrap math in $ signs.
- Write fractions as "1/2", mixed numbers as "3 1/5" (whole number,
  space, then fraction).
- Use the actual symbols: \u00f7 for division, \u00d7 for multiplication,
  \u00b7 for dot product, \u00b1 for plus-or-minus. Or write them out in
  words ("divided by", "times").
- Plain text only. The output renders as PDF, not as a TeX document."""


def _skill_pack_prompt(
    skill: str,
    cluster_label: str,
    cluster_centroid: float,
    cluster_size: int,
    centroid_for_skill: float,
    prereqs_for_skill: list[str],
    subject: str,
    skills_already_mastered: list[str],
) -> str:
    prereq_text = (
        ", ".join(prereqs_for_skill) if prereqs_for_skill
        else "(none -- this is a root skill)"
    )
    mastered_text = (
        ", ".join(skills_already_mastered[:6]) if skills_already_mastered
        else "(no skills yet mastered)"
    )
    return (
        f"Subject: {subject}\n"
        f"Target skill: {skill}\n"
        f"Prerequisites the cluster's students should be ready with: {prereq_text}\n"
        f"Skills the cluster has at least partial mastery of: {mastered_text}\n\n"
        f"This remediation will be taught to {cluster_size} students in a cluster "
        f"labelled '{cluster_label}' (cluster mean mastery {cluster_centroid:.2f}, "
        f"their mastery on {skill} is {centroid_for_skill:.2f}).\n\n"
        f"Generate a teaching pack for {skill} containing: explanation, one worked "
        f"example, exactly 3 practice items (with options A/B/C/D, correct option, "
        f"and a hint), and 2-3 common mistakes. Tailor difficulty to a student "
        f"who has the listed prerequisites but lacks {skill} itself."
    )


# ---- main entry point -----------------------------------------------------

def generate_remediation_pack(
    cluster_paths_xlsx: str | Path,
    cluster_summary_xlsx: str | Path,
    mastery_clustered_xlsx: str | Path,
    item_bank_json: str | Path,
    output_json: str | Path,
    *,
    client=None,
    markdown_dir: str | Path | None = None,
    progress: bool = True,
    resume: bool = False,
) -> dict:
    """Build a structured remediation pack per cluster.

    Returns the pack as a dict; writes it to output_json after every step
    so a crash never loses more than the in-flight LLM call. With
    resume=True, picks up from an existing output_json and only generates
    the steps that aren't already present. If markdown_dir is given, also
    write one .md file per cluster with the formatted content.
    """
    paths = pd.read_excel(cluster_paths_xlsx)
    summary = pd.read_excel(cluster_summary_xlsx)
    mastery = pd.read_excel(mastery_clustered_xlsx)
    bank = json.loads(Path(item_bank_json).read_text(encoding="utf-8"))
    skills = bank["meta"]["node_order"]
    subject = bank["meta"].get("subject", "Mathematics")
    prereqs = bank["meta"].get("prerequisites", {})

    if client is None:
        client = OllamaClient()

    out = Path(output_json)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Resume from a previous run if asked AND the file exists. Otherwise
    # start fresh. We use str cluster keys because JSON normalises them
    # anyway and round-tripping with int keys would silently break.
    if resume and out.exists():
        pack = json.loads(out.read_text(encoding="utf-8"))
        if progress:
            done = sum(len(c.get("path", [])) for c in pack["clusters"].values())
            print(f"resume: loaded existing pack with {done} step(s) already done")
    else:
        pack = {
            "subject": subject,
            "bank_quiz_context": bank["meta"].get("curriculum_context", ""),
            "clusters": {},
        }

    def _save():
        out.write_text(
            json.dumps(pack, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    cluster_ids = sorted(paths["cluster"].unique())
    for c in cluster_ids:
        c_key = str(int(c))
        c_path = paths[paths["cluster"] == c]
        c_summary = summary[summary["cluster"] == c].iloc[0]
        c_members = mastery[mastery["cluster"] == c]

        if c_path.iloc[0]["skill"] == "(all mastered)":
            pack["clusters"][c_key] = {
                "label": c_summary["label"], "n_students": int(c_summary["size"]),
                "path": [], "note": "all skills mastered, no remediation needed",
            }
            _save()
            continue

        # Skills the cluster has at least partial mastery of
        centroid = c_members[skills].mean()
        mastered = [s for s in skills if centroid[s] >= 0.5]

        # If resuming, reuse the partial record; else start fresh
        if c_key in pack["clusters"] and "path" in pack["clusters"][c_key]:
            cluster_record = pack["clusters"][c_key]
            done_steps = {s["step"] for s in cluster_record.get("path", [])}
        else:
            cluster_record = {
                "label": c_summary["label"],
                "n_students": int(c_summary["size"]),
                "mean_mastery": float(c_summary["mean_mastery"]),
                "path": [],
            }
            pack["clusters"][c_key] = cluster_record
            done_steps = set()

        for _, step in c_path.iterrows():
            step_n = int(step["step"])
            skill = step["skill"]
            if step_n in done_steps:
                if progress:
                    print(f"  cluster {c} step {step_n}: {skill}  "
                          f"(already in pack, skipping)")
                continue

            if progress:
                print(f"  cluster {c} step {step_n}: {skill}", flush=True)

            prompt = _skill_pack_prompt(
                skill=skill,
                cluster_label=c_summary["label"],
                cluster_centroid=c_summary["mean_mastery"],
                cluster_size=c_summary["size"],
                centroid_for_skill=step["centroid_mastery"],
                prereqs_for_skill=prereqs.get(skill, []),
                subject=subject,
                skills_already_mastered=mastered,
            )
            result = client.generate_json(
                prompt, schema=SkillPack, system=SYSTEM_PROMPT,
            )

            cluster_record["path"].append({
                "step": step_n,
                "skill": skill,
                "depth": int(step["depth"]) if not pd.isna(step["depth"]) else None,
                "band": step["band"],
                "centroid_mastery": float(step["centroid_mastery"]),
                "content": result.model_dump(),
            })
            _save()   # checkpoint: every step lands on disk before the next

    _save()
    print(f"\nwrote {out}")

    if markdown_dir is not None:
        _write_markdown(pack, Path(markdown_dir))

    return pack


def _write_markdown(pack: dict, out_dir: Path) -> None:
    """Render the pack as one markdown file per cluster, suitable for
    printing or attaching to a lesson plan."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for c_id, c in pack["clusters"].items():
        lines = [
            f"# Remediation plan — cluster {c_id} ({c['label']})\n",
            f"_Students in this group: {c['n_students']}_  ",
        ]
        if c.get("note"):
            lines.append(f"\n> {c['note']}\n")
            (out_dir / f"cluster_{c_id}.md").write_text(
                "\n".join(lines), encoding="utf-8")
            continue

        lines.append(f"_Mean mastery: {c['mean_mastery']:.2f}_  \n")
        for step in c["path"]:
            content = step["content"]
            lines.append(f"## Step {step['step']}: {step['skill']}")
            lines.append(
                f"_Cluster mastery on this skill: "
                f"{step['centroid_mastery']:.2f} — band: {step['band']}_\n"
            )
            lines.append(f"### Explanation\n\n{content['explanation']}\n")

            we = content["worked_example"]
            lines.append("### Worked example")
            lines.append(f"\n**Problem:** {we['problem']}\n")
            lines.append("**Solution:**")
            for i, st in enumerate(we["solution_steps"], 1):
                lines.append(f"{i}. {st}")
            lines.append(f"\n**Answer:** {we['final_answer']}\n")

            lines.append("### Practice")
            for i, item in enumerate(content["practice_items"], 1):
                lines.append(f"\n**Q{i}.** {item['stem']}")
                for letter, opt in item["options"].items():
                    marker = " ✓" if letter == item["correct_option"] else ""
                    lines.append(f"  - {letter}) {opt}{marker}")
                lines.append(f"  _Hint:_ {item['hint']}")

            lines.append("\n### Common mistakes to watch for")
            for m in content["common_mistakes"]:
                lines.append(f"\n- **{m['name']}** — {m['description']}  ")
                lines.append(f"  _Correction:_ {m['correction']}")

            lines.append("\n---\n")

        path = out_dir / f"cluster_{c_id}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"wrote {path}")


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cluster-paths", required=True)
    ap.add_argument("--cluster-summary", required=True)
    ap.add_argument("--mastery-clustered", required=True)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--output", required=True,
                    help="path to write remediation_pack.json")
    ap.add_argument("--markdown-dir", default=None,
                    help="if set, also write per-cluster .md files")
    ap.add_argument("--pdf", default=None,
                    help="if set, also write a combined PDF to this path")
    ap.add_argument("--resume", action="store_true",
                    help="resume from existing --output JSON; skip steps "
                         "already present")
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default=None)
    args = ap.parse_args()

    client_kwargs = {}
    if args.model:  client_kwargs["model"] = args.model
    if args.host:   client_kwargs["host"] = args.host
    client = OllamaClient(**client_kwargs)

    generate_remediation_pack(
        cluster_paths_xlsx=args.cluster_paths,
        cluster_summary_xlsx=args.cluster_summary,
        mastery_clustered_xlsx=args.mastery_clustered,
        item_bank_json=args.bank,
        output_json=args.output,
        client=client,
        markdown_dir=args.markdown_dir,
        resume=args.resume,
    )

    if args.pdf:
        build_remediation_pdf(args.output, args.pdf)


if __name__ == "__main__":
    _main()
