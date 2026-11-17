# Curriculum benchmarks, OCR and classroom pilots

Development version **0.4.0**, 2026-11-16. Records remain local to each workspace
and are included in workspace backups. This release provides validation tooling;
it does not establish educational effectiveness.

The initial validation scope is now Grades 1–3, while the application remains
open to other learner levels. No reviewer is assigned yet. Use the
[Grades 1–3 preparation pack](grade_1_3_validation_pack/README.md) to assemble
materials before reviewer recruitment. Curriculum authority and language remain open.

## Scanned curriculum documents

Open **Topic & Items → Scanned PDF transcription**. Upload a PDF, select physical
page numbers (for example `6-8`) and an OCR language code. At most 20 pages are
processed per run. **Run OCR in background** creates an ordinary recorded job;
its inputs, code, environment and engine version are preserved. No LLM is called.

OCR needs the separate [Tesseract engine and language data](https://tesseract-ocr.github.io/tessdoc/Installation.html).
The engine is **not installed on the current development machine**. Installing
Python dependencies alone does not install it. Add its executable to PATH, or
set `TESSERACT_CMD` to the executable's absolute path before starting Streamlit:

```powershell
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
python -m streamlit run ui/app.py
```

The [Tesseract command-line documentation](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)
describes language selection and TSV output. The app uses TSV word confidences,
but confidence is not proof of correctness. Missing language packs produce a
failed job with an error record. Cancellation prevents adoption; an external OCR
engine process may finish its current page after the Python worker is stopped.

After OCR finishes, refresh Topic & Items and select the completed run. Compare
the rendered page with the editable text. Correct mathematical signs, numbers,
headings and table reading order. Every selected page needs readable text.
For a blank/irrelevant page, select a smaller page set instead of inventing text.
**Manual transcription** works without Tesseract and uses the same review gate.

A named reviewer must confirm the transcription before it becomes a curriculum
source. Corrections create a new extraction version; they do not replace earlier
text. The original PDF hash, page identifiers, raw OCR output, corrected text,
reviewer and timestamp remain available in `curriculum_sources/*.review.json`.
The skill map records both the original document hash and extraction version.
Saving a transcription never approves a skill map.

Rendering limits each page to 12 million pixels with a scale cap of three.
OCR of handwriting, complex formula layouts and tables has not been validated;
image/text review remains necessary. The current automated tests use a fake OCR
transport, not a live Tesseract installation.

## Real-curriculum benchmark workflow

An initial public fixture comes from the Department for Education's
[England primary mathematics curriculum](https://www.gov.uk/government/publications/national-curriculum-in-england-mathematics-programmes-of-study).
Its 47 PDF pages all returned text in the local extraction smoke test.
`benchmarks/source_catalog.json` records the exact URL, retrieval time and hash.
This is a real-document extraction check, **not a measured model-accuracy result**.

1. Choose a small curriculum unit and save its source in Topic & Items. Retain
   the document edition, country/authority, grade and language in the case notes.
2. Have a subject expert prepare a reference skill map from the source, before
   seeing model proposals. Use `docs/templates/reference_map.example.json` as a
   format example only. Replace every example skill with expert-authored content.
3. In **Validation Studies → Curriculum benchmark**, freeze the reference,
   source sections, unit name, expert identity and split. Use development cases
   for prompt changes and separate evaluation cases for final comparison. Split
   by document/unit so near-duplicate pages do not cross the boundary.
4. Generate a candidate from the **same source version, sections and unit name**.
   Do not put the reference map in the generation prompt. Repeat across desired
   model/settings combinations using separately recorded generation jobs.
5. An expert explicitly aligns candidate skills to reference skills. Unmatched
   skills stay unmatched. One-to-one matching makes split/merged skills visible;
   it is a conservative scoring choice, not a semantic equivalence algorithm.
6. Judge each explicit claim as supported, unsupported or uncertain. Finding the
   quoted words in the document is insufficient: the words must support the claim.
   Record review minutes and correction rationale. A second reviewer can submit
   another record for the same case/candidate; previous reviews are retained.

Reports include skill and prerequisite precision/recall, missing/extra skills,
missing prerequisite edges, explicit-claim support fraction, uncertainty counts
and review time. A missing denominator is reported as null, not perfect accuracy.
Reference maps saved after a candidate are flagged as retrospective/exploratory.
Expert judgments and independence remain self-reported; there is no authenticated
reviewer system. Scores inherit uncertainty and disagreement in the reference.
No inter-rater reliability estimate or aggregate model ranking is claimed yet.

Benchmark cases and reviews are immutable records in `validation_records` inside
`assessment.sqlite`. Each scored review also saves its source archive and JSON in
`benchmark_reviews/<version>/`. Download review records from the UI for analysis.
Record failed generation attempts from Jobs & History when comparing models;
scoring successful outputs alone would hide failure rates.

## Classroom validation workflow

Use the [pilot worksheet](templates/pilot_worksheet.md) with the participating
teacher. Start with feasibility and diagnostic agreement. A before/after score
change alone does not establish that the intervention caused learning gains.

1. Review the question bank and skill map in Expert Review. Pilot analysis requires
   approval of every scope for the exact frozen bank version.
2. Prepare a separate assessment and a prespecified mastery rubric. Use different
   item identifiers and genuinely different evidence; renaming diagnostic items
   does not make them independent. Keep the reference assessor unaware of model
   diagnoses until their judgments have been recorded.
3. In **Validation Studies → Classroom pilot**, freeze the cohort description,
   responsible teacher, reference procedure and IDs, bank version, split seed and
   held-out fraction **before collecting data**. Changes create a new protocol.
4. Collect diagnostic responses through the existing paper/scan correction
   workflow. Record a diagnostic assessment ID and timezone-aware collection time.
5. Collect independent skill judgments using the downloadable CSV template:
   `student_idx, skill, mastered, assessment_id, collected_at, assessor`. Use
   matching pseudonymous student IDs and binary rubric outcomes, one row per
   student/skill. Do not upload student names when pseudonyms will suffice.
6. Upload both datasets and run the pilot evaluation. The backend rejects changed
   bank versions, reused assessment/item IDs, duplicate judgments, unknown students
   or skills, missing assessor details, future dates and collection before the
   frozen protocol. Separate IDs and a confirmation cannot mechanically prove
   independence; the teacher/researcher must implement it in practice.

The existing evaluation module compares simple scoring and fixed-parameter DINA
on identical within-student held-out responses, then compares skill estimates with
the separate criterion. Parameter sensitivity remains prespecified. The report
shows coverage and excluded/unmeasured cases. It is descriptive: it does not prove
generalization to other schools, estimate causal effects, or supply a defensible
sample-size calculation for a particular study design.

Each pilot evaluation preserves input records, bank, protocol reference, source
archive, implementation hash and report checksum under `pilot_evaluations/`.
Identical inputs and source reuse the same run rather than counting it twice.
Changed inputs or implementation produce a different run. Follow-up assessments
remain in the existing Follow-up workflow; they are not automatically treated as
an independent criterion or evidence of causal learning gains.

## What is needed from the project owner

- Target curriculum document/link and edition, subject, grade/year and language;
  include a representative scanned copy if OCR is important for deployment.
- A subject expert to author references independently and ideally a second expert
  to review disagreements, prerequisite judgments and the assessment rubric.
- Tesseract plus the needed language data on the machine that will run OCR.
- A participating teacher/classroom, agreed data-collection arrangements, separate
  assessment materials and pseudonymous results when ready.

Choose the final study design and sample size with the academic supervisor or
research collaborator before a confirmatory study. Keep development cases separate
from the final evaluation set and document exclusions and missing responses.
