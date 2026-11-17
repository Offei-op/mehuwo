# mehuwo

**Curriculum-guided mathematics diagnostics for paper-based classrooms.**

mehuwo helps teachers turn paper assessment responses into evidence about specific
skills and plan what to assess or teach next. Teachers can upload a curriculum,
review an AI-proposed skill map, prepare questions and printed materials, correct
scan recognition results, and examine diagnostic and follow-up reports.

The project is a local research prototype. It is not tied to Ghana or a particular
school system. The first planned validation covers **Grades 1-3**, while learner
levels remain configurable. The bundled fractions example is a demonstration,
not a validated Grades 1-3 assessment.

**Development version:** 0.4.0 | [Change history](CHANGELOG.md)

## What it does

| Area | Available workflow |
|---|---|
| Curriculum | PDF, DOCX, TXT and Markdown extraction; source-linked proposed skills and prerequisites |
| Scanned curricula | Local Tesseract OCR or manual transcription, with page-image correction and review |
| Expert review | Version-specific approval of maps, question answers, skill assignments and prerequisites |
| Question banks | Bundled, uploaded or generated mathematics questions; arithmetic and structural checks |
| Paper assessment | Quiz PDFs, answer keys and marked roster sheets; form manifests preserve shuffled question and option identities |
| Recognition | Scan processing with a correction screen before scoring |
| Diagnosis | Fixed-parameter DINA skill probabilities, uncertainty and a simple-scoring baseline |
| Teaching support | Group profiles, prerequisite-based teaching paths, generated narratives and printable materials |
| Follow-up | Additional assessments with item identities, timestamps and correction history |
| Evaluation | Held-out response comparisons, parameter sensitivity and independent skill judgments |
| Validation studies | Expert reference-map benchmarks and frozen classroom pilot protocols |
| Recovery | Background jobs, linked retries, version records and workspace backup/restore |

AI-generated maps are drafts. A human must approve the exact map version before
new question generation. Arithmetic verification checks the generated solution
against the answer; it does not establish that a question measures the right skill
or is suitable for the learner. Those judgments still need expert review.

## Quick start

The tested environment is **Windows with Python 3.14.7**. Other platforms and
Python versions have not been verified for this release.

```powershell
git clone https://github.com/Offei-op/mehuwo.git
cd mehuwo
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-tested.lock
.\.venv\Scripts\python -m streamlit run ui/app.py
```

Open the address printed by Streamlit, normally `http://localhost:8501`.
Create a workspace or open the bundled demo. You can inspect bundled materials
and use non-generation workflows without a running language model. The demo does
not provide a completed, classroom-validated study.

`requirements-tested.lock` records the tested application/test environment.
`requirements.txt` also contains broader experimental dependencies, including
optional machine-learning packages; it is not the pinned quick-start environment.

### Local language model

AI proposal and authoring steps use [Ollama](https://ollama.com/). The configured
default model is `gemma4:e4b`; generation requires a compatible locally available
model. Start Ollama and obtain the model you intend to use before generation:

```powershell
ollama serve
ollama pull gemma4:e4b
```

If Ollama is already running, a second `serve` process is unnecessary. The default
host is `http://localhost:11434`. Model choices are available in the generation
forms. Downloading dependencies and models needs network access; generation can
run locally afterwards. A remotely configured Ollama host receives its inputs.

### Optional curriculum OCR

Install the [Tesseract engine and language data](https://tesseract-ocr.github.io/tessdoc/Installation.html)
separately. Add the executable to PATH or set its absolute path before starting
Streamlit:

```powershell
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

Use **Topic & Items > Scanned PDF transcription**. Review all selected pages
against their images before saving the text. Manual transcription works without
Tesseract. Curriculum OCR and recognition of marked assessment sheets are separate
workflows; assessment-sheet recognition does not require Tesseract.

## Teacher workflow

1. **Topic & Items:** upload a curriculum and select a small unit. Request a map
   proposal, or load an existing map. Review evidence and inferred prerequisites,
   record approval, then activate the approved version.
2. **Item bank / Expert Review:** load or generate questions. Review answers,
   required skills and the bank's prerequisite relationships.
3. **Materials:** produce the quiz, answer key and roster sheets. Keep their form
   manifests and scan metadata together.
4. **Score & Mastery:** upload responses or process roster scans. Correct recognized
   values before scoring and inspect uncertain or unmeasured skills.
5. **Clusters & Path:** inspect group profiles and proposed teaching or assessment
   priorities. Group summaries do not replace individual judgments.
6. **Author & Print:** generate teaching content and narratives, review the output,
   and print the relevant materials.
7. **Follow-up:** select or create additional questions, record new responses and
   compare the updated diagnosis.

Long generation steps run outside the browser. Open **Jobs & History** to refresh
status, inspect logs, cancel or retry. Successful outputs require **Use this result**;
curriculum proposals return to human review. Old results cannot replace current
work when their input versions no longer match.

## Research and validation

The default diagnostic model is fixed-parameter **DINA**: each question specifies
its required skills, and inference accounts for assumed guessing and slipping.
Parameters are not fitted or calibrated automatically. Probabilities and suggested
teaching actions depend on these assumptions and the quality of the skill map.

**Evaluation** compares simple scoring and DINA using the same within-student
held-out responses and reports sensitivity to assumptions. Separate assessment
judgments can be used to evaluate skill estimates. **Validation Studies** adds
independently prepared reference maps and pilot protocols frozen before collection.

The [Grades 1-3 preparation pack](docs/grade_1_3_validation_pack/README.md) includes
case inventories, expert-review worksheets, independent-assessment planning sheets
and observation logs. The target curriculum authority/language, expert reviewers
and participating classroom are still to be selected.

Current evidence and limits:

- **64 tests passed locally** on the recorded Windows/Python environment. One
  existing SciPy plotting warning remains for identical clustering profiles.
- The official England primary mathematics PDF returned text on **47/47 pages**.
  This is an extraction smoke test, not a measurement of model accuracy.
- OCR transport tests use a fake engine; live Tesseract accuracy has not been
  measured in this environment.
- Real-model curriculum accuracy, classroom diagnostic validity and learning
  benefits have not yet been established.
- Reviewer identities are self-reported. The application has no authenticated
  multi-user access or institutional authorization system.
- Current generation and verification target mathematics. Other subjects need
  additional question validation and diagnostic design.

## Records, versions and backups

Each workspace normally lives under `data/runs/`. It contains the active workspace
manifest, assessment/review databases, source documents, versioned artifacts and
job records. Background runs preserve input snapshots, exact source archives,
model settings and exchanges, output hashes and failure history. Retries retain
links to prior attempts. These records can contain student information.

New default workspace directories and downloaded benchmark PDFs are ignored by
Git. Do not commit classroom records or model logs. Workspace conflict detection
prevents a stale browser session from silently overwriting a newer manifest.

Create and download a backup in **Jobs & History**, between editing sessions and
with generation stopped. Restore into a new directory:

```powershell
.\.venv\Scripts\python -m assessment.backups C:\Backups\workspace.zip data\runs\restored_class
```

Existing workspaces are not overwritten. Restore checks archive contents and
SQLite integrity. Cross-operating-system restoration of Windows-authored workspace
paths has not been validated. See [records and recovery](docs/engineering_records.md).

## Repository layout

| Path | Purpose |
|---|---|
| `ui/` | Streamlit application and workflow pages |
| `assessment/` | Curriculum review, OCR, jobs, evaluation, follow-up and study records |
| `agent/` | Local-model client, question generation and teaching content |
| `curriculum/` | Topic specifications, question-bank validation and quiz rendering |
| `recognition/` | Roster generation, scan recognition, scoring and diagnostic model |
| `mastery/` | Grouping and teaching-path logic |
| `benchmarks/` | Public-document downloader and source/checksum catalog |
| `tests/` | Offline regression and workflow tests |
| `docs/` | Model details, workflows, engineering records and validation materials |

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q
```

The suite does not require Ollama or Tesseract. The public-document check runs only
when its downloaded fixture is present; otherwise it is skipped. Fetch it explicitly
when network access is available:

```powershell
.\.venv\Scripts\python benchmarks/fetch_public_curriculum.py
```

The optional live model tool-calling check is separate:

```powershell
.\.venv\Scripts\python tests/test_tool_calling.py
```

A Windows GitHub Actions workflow is configured for offline regression tests.
Hosted CI results must be checked separately from the recorded local test results.

## Documentation

- [Curriculum extraction and approval](docs/curriculum_maps.md)
- [Diagnostic model and assumptions](docs/diagnostics.md)
- [Review, scanning, evaluation and follow-up](docs/pilot_workflows.md)
- [OCR, benchmarks and classroom validation](docs/validation_studies.md)
- [Grades 1-3 validation materials](docs/grade_1_3_validation_pack/README.md)
- [Engineering records and recovery](docs/engineering_records.md)
- [UI details](ui/README.md)
- [Version history](CHANGELOG.md)

## Contributing

Use a feature branch and include the problem, resulting behavior and relevant
validation in the change description. Preserve approval gates and immutable item,
map and bank identities. Add regression checks for changes to scoring, data
contracts or recovery, and update the change history and workflow documentation.
Use synthetic or suitably de-identified fixtures instead of classroom records.

## Author

Designed and built by **Papa Offei Obuobisah Bekoe**.
