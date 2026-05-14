# mehuwo

**Adaptive math diagnostics for Ghanaian classrooms.**

`mehuwo` is an end-to-end pipeline that takes a curriculum specification, has a local LLM author and arithmetically verify a multiple-choice diagnostic item bank, prints paper quizzes and class roster sheets with ArUco fiducial markers, ingests the filled-in sheets, scores per-skill mastery, clusters students by mastery profile, walks the prerequisite skill graph to build a remediation path for each cluster, and finally has the LLM author teacher-facing remediation packs and prints them as PDFs.

The pilot domain is the **Fractions** unit for upper-primary / JHS (B.S. 4–6) mathematics. Items, names, currencies (GH₵), and contexts (jollof, kelewele, market scenes) are localised to Ghana.

![Skill graph of the fractions unit](curriculum/Question%20Bank/Skill%20graph%20of%20fractions%20unit.png)

---

## Table of contents

1. [Why](#why)
2. [How it works](#how-it-works)
3. [Repository layout](#repository-layout)
4. [Installation](#installation)
5. [Quickstart: end-to-end run](#quickstart-end-to-end-run)
6. [The pipeline in detail](#the-pipeline-in-detail)
7. [Data contracts](#data-contracts)
8. [Design notes](#design-notes)
9. [Project status and roadmap](#project-status-and-roadmap)
10. [Tests](#tests)
11. [Tech stack](#tech-stack)
12. [Acknowledgements](#acknowledgements)

---

## Why

Public-school teachers in Ghana administer the same paper-and-pencil tests that have been used for decades. Marking is manual, slow, and yields a single bottom-line score that hides *which* skills the student missed. By the time the teacher sees the pattern, the term has moved on.

`mehuwo` turns a marked paper test into a **per-skill mastery profile** in minutes, groups students into pedagogically meaningful clusters, and hands the teacher a printed remediation pack that targets exactly the gaps a given cluster has — sequenced through the prerequisite graph so foundations come before leaves.

The system is built to run offline on modest hardware: a local Ollama daemon, a laptop, a printer, and a phone camera or document scanner.

---

## How it works

```
  topic_spec.json
        │
        ▼
  ┌──────────────────────────┐
  │  agent/bank_generator.py │  LLM agent loop with arithmetic verifier
  └──────────────────────────┘
        │
        ▼
  item_bank.json  ──►  curriculum/validate_bank.py  (schema gate)
        │
        ├──►  curriculum/make_quiz_pdf.py   ──►  quiz.pdf + quiz_key.pdf
        │
        └──►  recognition/score_sheet_template.py   ──►  roster.pdf + cells.json
                                                              │
                                                              ▼
                                       [class fills in roster sheet on paper]
                                                              │
                                                              ▼
                                       [OCR pipeline — NOT YET BUILT]
                                                              │
                                                              ▼
                                                       results.xlsx
                                                              │
        ┌──────────────────────────────────────────────────────┘
        ▼
  recognition/scoring_layer.py   ──►  mastery.xlsx
        │
        ▼
  mastery/clustering_layer.py    ──►  mastery_clustered.xlsx, cluster_summary.xlsx
        │
        ▼
  mastery/remediation_layer.py   ──►  cluster_paths.xlsx
        │
        ├──►  agent/cluster_narrative.py     ──►  cluster_summary_with_narratives.xlsx
        │           │
        │           ▼
        │     agent/cluster_report_pdf.py    ──►  cluster_report.pdf
        │
        └──►  agent/remediation_content.py   ──►  remediation_pack.json
                    │
                    ▼
              agent/remediation_to_pdf.py    ──►  remediation_pack.pdf
```

Every box is a standalone CLI script; the JSON / XLSX files between them are stable contracts you can hand-inspect or replace.

---

## Repository layout

```
mehuwo/
├── curriculum/                    Topic specs, item banks, quiz PDFs
│   ├── Topic Specs/
│   │   └── topic_spec_fractions.json
│   ├── Question Bank/
│   │   ├── fraction_diagnostic_item_bank.json
│   │   └── Skill graph of fractions unit.png
│   ├── make_quiz_pdf.py           Render bank to A4 quiz + answer key PDFs
│   ├── qmatrix.py                 Extract Q-matrix from a bank
│   ├── qmatrix_render.py          Render Q-matrix as SVG
│   ├── validate_bank.py           Structural / semantic checks on a bank
│   ├── quiz.pdf                   Example output
│   └── quiz_key.pdf
│
├── agent/                         LLM-powered generation and authoring
│   ├── llm_client.py              Ollama wrapper: generate / json / agent loop
│   ├── bank_generator.py          Agentic item-bank author with arithmetic verifier
│   ├── cluster_narrative.py       LLM diagnostic narratives per cluster
│   ├── remediation_content.py     LLM teaching micro-pack per (cluster, skill)
│   ├── cluster_report_pdf.py      Cluster diagnostic report (PDF)
│   ├── remediation_to_pdf.py      Remediation pack (PDF)
│   ├── pdf_fonts.py               Register a unicode-safe font
│   └── pdf_text.py                Sanitise LLM output for reportlab paragraphs
│
├── recognition/                   Paper sheet generation + scoring
│   ├── score_sheet_template.py    A4 roster sheet with ArUco fiducials
│   └── scoring_layer.py           Per-item correctness → per-skill mastery
│
├── mastery/                       Clustering + path planning
│   ├── clustering_layer.py        Ward hierarchical clustering on mastery vectors
│   └── remediation_layer.py       Topo-sort deficits through the prerequisite DAG
│
├── tests/
│   ├── test_tool_calling.py       Smoke test: does this Ollama+Gemma honour tools?
│   └── scoring_layer.py           Stub
│
├── api/                           (empty — planned FastAPI service)
├── data/                          (empty — planned data store)
├── ui/                            (empty — planned Streamlit UI)
│
├── requirements.txt
├── roster_test.pdf                Example: 120 students × 26 items, 3 pages
├── roster_test_cells.json         Example: cell metadata for the above
└── README.md                      This file
```

---

## Installation

### 1. Python environment

Python 3.11+ is recommended.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Local LLM (Ollama)

The agent layer runs against a local [Ollama](https://ollama.com/) daemon. Default model is `gemma4:e4b` (a small thinking-capable Gemma variant with native tool calling). The model name is overridable via `--model` everywhere it's used.

```bash
# Install Ollama, then pull the model:
ollama pull gemma4:e4b

# Verify tool calling works for this build before running bank_generator:
python tests/test_tool_calling.py
```

If the smoke test prints a `tool_calls` list, you're good. If it prints `None`, your Ollama build does not have function-calling wired up for this model and `bank_generator.py` won't be able to do its work — try a newer Ollama, or a different tool-calling-capable model via `--model`.

### 3. Optional: OpenCV with `contrib` modules

ArUco lives in the `contrib` package. Make sure `opencv-contrib-python` is installed (not plain `opencv-python`) before generating roster sheets.

---

## Quickstart: end-to-end run

The example below runs the full pipeline against the bundled `topic_spec_fractions.json` and produces a teacher-facing remediation report. It assumes you've completed installation and that `ollama serve` is running.

```bash
# 1) Generate an arithmetic-verified item bank from the topic spec
python agent/bank_generator.py \
    --spec curriculum/Topic\ Specs/topic_spec_fractions.json \
    --output work/bank.json \
    --target-per-skill 2 --target-multi 6

# 2) Validate the bank against the pipeline's schema
python curriculum/validate_bank.py work/bank.json

# 3) Print the quiz + answer key
python curriculum/make_quiz_pdf.py work/bank.json \
    --output work/quiz.pdf --key-output work/quiz_key.pdf

# 4) Generate the class roster (one tick column per item)
#    Currently this is a library call; see "The pipeline in detail" for usage.
python recognition/score_sheet_template.py
#    Produces roster_test.pdf + roster_test_cells.json in CWD as a demo.

# --- after class sits the quiz and the recognition pipeline writes
#     results.xlsx (one row per student, item_1..item_N + scored flag) ---

# 5) Convert per-item correctness to per-skill mastery
python recognition/scoring_layer.py \
    --results work/results.xlsx \
    --bank work/bank.json \
    --output work/mastery.xlsx

# 6) Cluster students by mastery profile
python mastery/clustering_layer.py \
    --mastery work/mastery.xlsx \
    --bank work/bank.json \
    --output work/mastery_clustered.xlsx \
    --summary work/cluster_summary.xlsx \
    --k 3 --debug-dir work/debug

# 7) Build a remediation path through the skill graph for each cluster
python mastery/remediation_layer.py \
    --mastery-clustered work/mastery_clustered.xlsx \
    --bank work/bank.json \
    --cluster-paths work/cluster_paths.xlsx

# 8) Author cluster-level diagnostic narratives (LLM)
python agent/cluster_narrative.py \
    --cluster-summary work/cluster_summary.xlsx \
    --bank work/bank.json \
    --output work/cluster_summary_with_narratives.xlsx

# 9) Author per-(cluster, skill) teaching micro-packs (LLM)
python agent/remediation_content.py \
    --cluster-paths work/cluster_paths.xlsx \
    --cluster-summary work/cluster_summary.xlsx \
    --mastery-clustered work/mastery_clustered.xlsx \
    --bank work/bank.json \
    --output work/remediation_pack.json

# 10) Print teacher-facing PDFs
python agent/cluster_report_pdf.py \
    --cluster-summary work/cluster_summary_with_narratives.xlsx \
    --bank work/bank.json \
    --output work/cluster_report.pdf

python agent/remediation_to_pdf.py \
    --pack work/remediation_pack.json \
    --output work/remediation_pack.pdf
```

---

## The pipeline in detail

### Topic spec

A small JSON file describes the subject, the list of skills (the `node_order`), the prerequisite DAG, optional short labels, skill descriptions, and the cultural context strings the LLM uses when writing items. See `curriculum/Topic Specs/topic_spec_fractions.json` for the reference example.

The topic spec is the only file you write by hand. Everything downstream is generated.

### Bank generation

```bash
python agent/bank_generator.py \
    --spec topic_spec.json \
    --output bank.json \
    --target-per-skill 2 --target-multi 6 \
    [--max-iterations 200] \
    [--model gemma4:e4b] [--host http://localhost:11434] \
    [--thinking | --no-thinking]
```

The model is given three tools — `coverage_report`, `verify_item`, `add_item` — and looped until coverage hits the targets or the iteration budget is exhausted. **Every item the model submits is arithmetically verified**: the `solution_expression` is parsed through a restricted AST (only `Fraction(...)` and arithmetic), evaluated, and compared to the parsed correct option. Items with mismatched arithmetic, duplicate options, wrong q-vector shape, missing distractor rationales, or misaligned target/q-vector are rejected with a specific error, which the model is asked to fix.

A continuation check rejects premature `DONE` responses when coverage is incomplete and detects the failure mode where the model writes an item as markdown prose instead of calling `add_item`.

`--no-thinking` disables Gemma's thinking mode for the agent loop — substantially faster per iteration on weak hardware, sometimes at the cost of math quality. Try it without, flip it on if you see arithmetic mismatches.

### Bank validation

```bash
python curriculum/validate_bank.py path/to/bank.json
```

Runs nine checks (S01–S09) including: unique IDs, q-vectors are binary and of length `len(node_order)`, the target node(s) are marked 1 in the q-vector, prerequisites reference only known skills, the DAG has no cycles. Fails loudly so a malformed bank doesn't silently produce wrong diagnoses downstream.

### Quiz + answer key

```bash
python curriculum/make_quiz_pdf.py bank.json \
    [--output quiz.pdf] [--key-output quiz_key.pdf] \
    [--shuffle-items] [--shuffle-options] [--seed 42] \
    [--include single_skill_items multi_skill_items]
```

Renders the bank to two A4 PDFs: the student-facing quiz (no answers) and the marker's key (item ID, correct letter, target node(s), q-vector). Unicode vulgar fractions in the source items (½, ⅓, ¾, ⅖ …) are normalised to slash notation because Helvetica only carries ½, ¼, ¾. Register a TTF font that covers the Number Forms block if you want native unicode rendering.

### Roster sheet

The roster generator is called as a Python function (`generate_roster_pdf(...)` in `recognition/score_sheet_template.py`). The `__main__` block at the bottom of that file demonstrates the call and produces `roster_test.pdf` and `roster_test_cells.json` in the working directory.

```python
from recognition.score_sheet_template import generate_roster_pdf

quiz_id, cells = generate_roster_pdf(
    students=["Student 001", "Student 002", ...],   # or real names
    num_items=26,
    output_path="roster.pdf",
    cell_metadata_path="roster_cells.json",
    quiz_title="JHS 2 Mathematics — Fractions Quiz",
    class_label="JHS 2A",
    school_name="Sample Public JHS",
    students_per_page=40,
)
```

Each page carries four **ArUco fiducials** (DICT_4X4_50) at the corners for perspective rectification. Page *k* uses IDs `[4k, 4k+1, 4k+2, 4k+3]` so the recognition pipeline can tell which page of a multi-page sheet it's looking at.

A deterministic **quiz_id** (SHA-256 of students + items + title, truncated) is printed on every page and embedded in `cells.json`. The recognition pipeline refuses to apply the wrong metadata to the wrong sheet — protection against the obvious failure mode of teachers feeding a scanned sheet against the cells JSON from a different quiz.

### Scoring

```bash
python recognition/scoring_layer.py \
    --results results.xlsx \
    --bank bank.json \
    --output mastery.xlsx \
    [--audit audit.xlsx] \
    [--partial-threshold 0.5] [--mastered-threshold 1.0]
```

`results.xlsx` (produced by the OCR step) has columns `student_idx`, `student_name`, `item_1` … `item_N`, `scored`. `scored=0` for any student whose sheet wasn't successfully processed; their mastery comes out as **NaN**, not zero. This distinction matters downstream — an unprocessed student is not a student who got everything wrong.

**Mastery on skill K is the mean correctness on the single-skill items whose `target_node == K`.** Multi-skill items are deliberately excluded from the primary score because they conflate skills; they appear in the optional audit file so disagreements with the pure-item signal are visible to the teacher.

### Clustering

```bash
python mastery/clustering_layer.py \
    --mastery mastery.xlsx --bank bank.json \
    --output mastery_clustered.xlsx \
    --summary cluster_summary.xlsx \
    --k 3 [--debug-dir debug/]
```

Hierarchical clustering on the per-skill mastery vectors (Ward linkage, Euclidean distance, cut at *k*). Clusters are **renumbered** so cluster 0 has the lowest mean mastery and *k*−1 the highest — a stable ordering for downstream remediation. Unscored students (any NaN in their skill columns) are excluded from the fit and labelled `cluster=-1`.

Each cluster is assigned a pedagogical label inferred from where the deficits sit in the prerequisite DAG: `foundation-gap`, `middle-gap`, `leaf-gap`, `broad-gap`, or `all-mastered`. The classifier uses the *mean centroid* per DAG-depth band, not a "fraction of skills below threshold" rule, which would flip the label every time a single skill crossed the threshold.

A silhouette score is computed at *k* ∈ {2..5} and flagged in the log if the chosen *k* yields a silhouette below 0.15 (clusters not well separated — consider a different *k*).

`--debug-dir` writes three validation plots:

- `dendrogram.png` — full hierarchy with the *k*-cut highlighted
- `silhouette.png` — silhouette score by *k* with the chosen *k* marked
- `heatmap.png` — cluster × skill centroid heatmap, RdYlGn colour scale

### Remediation path

```bash
python mastery/remediation_layer.py \
    --mastery-clustered mastery_clustered.xlsx \
    --bank bank.json \
    --cluster-paths cluster_paths.xlsx \
    [--student-paths student_paths.xlsx] \
    [--partial-threshold 0.5]
```

Each cluster's deficit skills are sequenced by Kahn-style topological sort of the *deficit subgraph* (edges only between deficient skills), with full-DAG depth as a tiebreaker when several deficits are simultaneously available. This ensures foundational deficits are addressed before parallel mid- or leaf-level deficits, even if there's no strict prerequisite link between them in the deficit subgraph.

Output: long-format `(cluster, step, skill, depth, band)` rows.

### Cluster narratives

```bash
python agent/cluster_narrative.py \
    --cluster-summary cluster_summary.xlsx \
    --bank bank.json \
    --output cluster_summary_with_narratives.xlsx \
    [--mastery-clustered mastery_clustered.xlsx \
     --student-output student_narratives.xlsx \
     --only-clusters 0,1] \
    [--model gemma4:e4b]
```

For each cluster, the LLM produces three short fields:

- `likely_understands` — what students in this cluster have likely mastered (2–3 sentences)
- `likely_misunderstands` — implied conceptual gaps and misconceptions (2–3 sentences)
- `teaching_watchpoints` — what to watch for during remediation (2–3 sentences)

Optionally also produces per-student narratives — useful for parent-teacher reports.

### Remediation content

```bash
python agent/remediation_content.py \
    --cluster-paths cluster_paths.xlsx \
    --cluster-summary cluster_summary.xlsx \
    --mastery-clustered mastery_clustered.xlsx \
    --bank bank.json \
    --output remediation_pack.json \
    [--markdown-dir packs_md/] [--pdf remediation_pack.pdf] \
    [--resume]
```

For every (cluster, skill) step in each cluster's path, the LLM authors:

- **explanation** — ~100-word in-context explanation
- **worked_example** — problem statement, numbered solution steps, final answer
- **practice_items** — exactly 3 MCQs with correct option and a one-line teacher hint
- **common_mistakes** — 2–3 named misconceptions, each with a one-sentence correction

The output is checkpointed after every step. `--resume` picks up from an existing `remediation_pack.json` and skips steps already present — re-run after a crash without losing the in-flight work.

### Printable PDFs

```bash
# Cluster diagnostic report (cover + per-cluster section)
python agent/cluster_report_pdf.py \
    --cluster-summary cluster_summary_with_narratives.xlsx \
    --bank bank.json --output cluster_report.pdf

# Remediation pack (combined PDF, or one PDF per cluster with --split)
python agent/remediation_to_pdf.py \
    --pack remediation_pack.json \
    --output remediation_pack.pdf
# or
python agent/remediation_to_pdf.py \
    --pack remediation_pack.json \
    --output-dir per_cluster_pdfs/ --split
```

---

## Data contracts

### Item bank JSON

```json
{
  "meta": {
    "subject": "Fractions",
    "node_order": ["Multiplication", "Division", "Concept_of_fraction", ...],
    "prerequisites": {
      "Multiplying_fractions": ["Multiplication", "Simplifying_fractions"],
      ...
    },
    "short_labels": {"Multiplication": "Mult", ...},
    "curriculum_context": "...",
    "design_notes": ["...", "..."]
  },
  "misconceptions": {
    "Multiplication": [
      {"label": "confuses_with_addition",
       "description": "Computes a sum instead of a product (e.g. 6 × 7 = 13)."},
      ...
    ]
  },
  "single_skill_items": [
    {
      "item_id": "MULT_001",
      "stem": "What is 6 × 7?",
      "options": ["13", "40", "42", "48"],
      "correct_answer": "42",
      "q_vector": [1,0,0,0,0,0,0,0,0,0],
      "target_node": "Multiplication",
      "difficulty_hint": "easy",
      "distractor_rationales": {
        "13": "confuses_with_addition (6 + 7 = 13)",
        "40": "off-by-two slip on the 6 times table",
        "48": "tables_breakdown_above_seven (computes 6 × 8 instead)"
      },
      "rationale": "Basic multiplication fact..."
    }
  ],
  "multi_skill_items": [
    {
      "item_id": "MULTI_001",
      "stem": "Compute 1¾ × 2/3.",
      "options": ["2/3", "7/6", "5/7", "21/12"],
      "correct_answer": "7/6",
      "q_vector": [1,0,1,0,0,1,0,1,0,0],
      "target_nodes": ["Mixed_to_improper", "Multiplying_fractions"],
      "difficulty_hint": "medium",
      "distractor_rationales": {...},
      "rationale": "Requires converting 1¾ to 7/4 and then multiplying..."
    }
  ]
}
```

### `results.xlsx`

Produced by the recognition step (not yet built). One row per student.

| student_idx | student_name | item_1 | item_2 | … | item_N | scored |
|-------------|--------------|--------|--------|---|--------|--------|
| 0           | Kofi Mensah  | 1      | 0      | … | 1      | 1      |
| 1           | Ama Boateng  | 0      | 0      | … | 0      | 0      |

`item_k = 1` if the student's tick is on the correct option for the *k*-th item (single-skill items first in bank order, then multi-skill items). `scored = 1` if the recognition pipeline successfully processed the student's row; `0` otherwise.

### `mastery.xlsx`

| student_idx | student_name | Multiplication | Division | … | PEDMAS | n_skills_mastered | n_skills_partial | mean_mastery |
|-------------|--------------|----------------|----------|---|--------|-------------------|------------------|--------------|

Per-skill values are in `{0.0, 0.5, 1.0}` or NaN (unscored).

### `cluster_summary.xlsx`

| cluster | size | mean_mastery | label | weakest_skills | centroid_Multiplication | … |
|---------|------|--------------|-------|----------------|--------------------------|---|

`cluster_paths.xlsx` is long-format: one row per `(cluster, step, skill, depth, band)`.

---

## Design notes

A handful of non-obvious choices that are worth flagging if you're reading the code or adapting it for a different subject.

### Arithmetic verification is a tool, not a post-hoc check

The dominant LLM failure mode for math content is silently-wrong arithmetic: the model writes a stem, builds plausible distractors, and either marks the wrong letter or computes the answer wrong. Tying the verifier into the tool loop (every `add_item` runs it; the LLM sees the failure as a tool error and is asked to fix it) catches this in the same turn the model produced the item, instead of after the bank is "finished" and a human is auditing 26 items by hand.

The verifier uses safe AST-restricted evaluation of a Python expression in the `Fraction` class — only arithmetic operators and the `Fraction` name are allowed; everything else raises. The model is required to provide both the textual options and the symbolic expression separately, so the verifier can prove the marked letter is consistent with the math.

### Multi-skill items don't count toward mastery

Mastery on skill *K* is the mean correctness on the single-skill items whose `target_node == K`. Multi-skill items intentionally do not contribute, because failing a multi-skill item only tells you the *chain* broke — not which link. They still appear in the audit file so a teacher can spot the case where pure items disagree with chained items (often a sign that the student has procedural fluency but not transfer).

### Cluster label uses band-mean, not threshold-crossing count

An earlier version classified a cluster as `foundation-gap` if a fixed fraction of foundation skills were below threshold. The label flipped every time a single skill crossed the line — small data perturbations changed the label. The current rule uses the *mean centroid* per DAG-depth band; small changes in the data produce small changes in the means, and the label is stable.

When multiple bands are simultaneously deficient, the label is `broad-gap` rather than the shallowest deficient band. A cluster whose foundation mean is 0.49 and leaf mean is 0.15 needs different remediation than a cluster whose foundation is 0.49 and leaf is 0.51 — flagging the breadth lets the remediation layer sequence appropriately rather than collapsing them into the same bucket.

### Roster sheets bind metadata to the printed paper

A deterministic `quiz_id` (truncated SHA-256 of `students + items + title`) is printed on every page of the roster sheet and embedded in `cells.json`. The recognition pipeline can therefore refuse to score a scanned sheet against the wrong metadata file — protection against the easy mistake of generating two quizzes in the same week and feeding the wrong `cells.json`.

Each page also carries its own block of four ArUco IDs (`[4k..4k+3]`), so a multi-page sheet whose pages get shuffled or scanned out of order can still be sorted and rectified correctly.

### Unicode vulgar fractions are normalised in the quiz PDF

Helvetica (reportlab's default) only carries ½, ¼, ¾ — not ⅓, ⅔, ⅕, ⅖, ⅗, ⅘, ⅙, ⅚, ⅛, ⅜, ⅝, ⅞. To keep the quiz looking consistent the renderer normalises all vulgar fractions to slash notation before rendering. If you want native unicode glyphs, register a TTF font covering the Number Forms block (DejaVu Sans is easiest), turn off the normaliser, and set the font on every paragraph style.

### Mojibake auto-repair

When a model is asked to produce ⅔ (UTF-8 bytes `0xC2 0xBE`) and the bytes get misdecoded as Latin-1, the string arrives as `Â¾`. The option parser detects the telltale `Â` / `â` lead byte and re-encodes — turning what would have been a verifier failure into a clean parse. Cheap to do, prevents a class of confusing rejections from the arithmetic verifier.

---

## Project status and roadmap

### What works today

| Layer | Status |
|-------|--------|
| Topic spec → arithmetic-verified item bank | **Working** |
| Bank validation gate | **Working** |
| Quiz PDF + answer key | **Working** |
| Roster sheet with ArUco fiducials + cell metadata JSON | **Working** |
| Scoring layer (results.xlsx → mastery.xlsx) | **Working** |
| Hierarchical clustering with silhouette validation + debug plots | **Working** |
| Topological remediation path through the DAG | **Working** |
| LLM cluster narratives | **Working** |
| LLM remediation packs (explain + worked example + practice + mistakes) | **Working** |
| Cluster report PDF | **Working** |
| Remediation pack PDF | **Working** |

### What's missing

- **Recognition / OCR step.** The roster sheet is printable and has fiducials + per-cell coordinates; the scoring layer consumes `results.xlsx`. The thing in the middle — open the scanned sheet, rectify against the ArUco corners, classify each tick cell, write `results.xlsx` — is not yet implemented. `opencv-contrib-python`, `pillow`, `numpy`, `torch`, and `torchvision` are in `requirements.txt` in anticipation (CNN fallback for the tick classifier). This is the critical missing middle.
- **API layer.** `api/` is empty; `fastapi` and `uvicorn` are in `requirements.txt` but no service is built.
- **UI.** `ui/` is empty; `streamlit` is in `requirements.txt`. The intent is a teacher-facing dashboard for uploading a scanned sheet and viewing the resulting reports.
- **Data store.** `data/` is empty; `sqlalchemy` is in `requirements.txt`. Currently every layer reads/writes files directly.
- **Add/subtract fractions, common denominator.** The fractions skill graph deliberately omits these (it's not a complete unit). A v2 of `topic_spec_fractions.json` should add them — PEDMAS items currently avoid `+/-` on fractions for this reason, which limits diagnostic range.
- **Calibration data.** No labelled set of filled-in sheets to measure tick-classification accuracy against once the CV step is built.

### Suggested next steps

1. Build the OCR pipeline: ArUco corner detection → homography → tick-cell crops → either an OpenCV threshold-on-fill heuristic or a small CNN classifier (the path the requirements file hints at). Calibrate against a labelled set of real filled-in sheets.
2. Extend the topic spec with Add/Sub fractions and Common denominator nodes; regenerate the bank.
3. Add a Streamlit UI that wraps the CLI steps for a non-technical user.
4. Unit tests around the verifier, the depth/topo functions, and the cluster-label classifier.

---

## Tests

```bash
# Verify your Ollama + model combo supports tool calling
python tests/test_tool_calling.py
```

Test coverage is currently shallow — a single smoke test for tool calling, plus a stub of the scoring layer. Adding tests around the arithmetic verifier in `bank_generator.py`, the `compute_depths` / `topo_path` functions in `mastery/`, and the `cluster_label` classifier in `clustering_layer.py` is a good first contribution.

---

## Tech stack

- **LLM:** local Ollama daemon, `gemma4:e4b` by default
- **Validation / schemas:** Pydantic
- **Symbolic math:** Python `fractions.Fraction`, AST-restricted `eval`
- **Clustering / stats:** SciPy hierarchical clustering, scikit-learn silhouette
- **Data:** pandas, openpyxl (via pandas), numpy
- **PDF:** reportlab
- **Computer vision (planned):** OpenCV with `contrib` (ArUco), Pillow, PyTorch (CNN fallback)
- **Plotting:** matplotlib (debug plots only)

---

## Acknowledgements

Designed and built by **Papa Offei Obuobisah Bekoe**.

The cognitive-diagnostic framing draws on standard KST and DINA-style ideas — items annotated with q-vectors against a skill graph, mastery inferred per skill, students clustered into pedagogically interpretable groups, remediation sequenced through the prerequisite DAG. The novelty here is the agentic, arithmetically-verified item authoring loop and the deliberate localisation to the Ghanaian classroom.