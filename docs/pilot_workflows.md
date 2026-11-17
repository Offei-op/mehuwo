# Review, evaluation, follow-up, and paper identities

Skill maps may be supplied directly or proposed by a local model from an uploaded
curriculum. Document-derived maps include cited source excerpts and distinguish
explicit claims from inferred prerequisites. Every map requires a separate human
approval before new LLM question generation. See [curriculum-map review](curriculum_maps.md).
Questions come from bundled banks, uploads, local Ollama generation, and teacher-authored
follow-ups. Arithmetic consistency does not establish that a question measures its assigned skills.

## Expert review (sidebar: Expert Review)

1. Load or generate a bank on Topic & Items.
2. Enter a reviewer name. Select `skill_map` to review/edit prerequisite JSON,
   or a question ID to inspect the wording, four options, correct answer, and skills.
3. For question changes, select **Apply these question edits to a new bank version**.
   Supply the revised fields. Removing a solution expression clears it; stale
   distractor rationales are removed when questions are revised.
4. Record `approved` or `needs_revision` with a rationale. A changed bank becomes
   the active version, clearing pointers to incompatible materials/results while
   retaining historical files. Editing the bank's map does not silently edit the
   topic specification used for future LLM generation; update that separately.

`assessment.sqlite` in the workspace stores immutable JSON bank snapshots (SHA-256
content versions) and timestamped review decisions, including parent versions.
Approvals apply to the exact bank version. **Make corrections first, then complete
the approvals**: changing any bank content requires review of the resulting version.
The latest decision per review target applies within that version. Materials shows
the approval count, but printing is not blocked. Reviewer names are self-reported;
this is a local single-user workflow, not authenticated institutional sign-off.

## Printed question identities

The quiz renderer now writes `quiz.form.json` beside each PDF. It records the bank
version, form ID, printed position -> item ID mapping, printed option order, and
correct answer letter. The quiz and answer key show the same form ID. New materials
are written under distinct directories rather than overwriting earlier forms.

Generate the quiz before its roster. Roster cell metadata embeds the form mapping
and its quiz ID depends on the form. One roster corresponds to one form. For classes
using multiple forms, process each roster/form separately; do not combine them as
if they shared question order.

The CLI scorer reads an embedded `form_manifest` worksheet automatically, or accepts
`--form-manifest quiz.form.json` for an unstamped spreadsheet in printed order. It
remaps correctness into original bank order. Canonical reviewed/imported results
carry an `assessment_meta` sheet; mismatched banks and double-remapping are rejected.
Legacy unstamped files cannot reveal their order automatically: the importer asks
the teacher to identify bank order or provide the matching printed-form manifest.
Mapping correctness does not require interpreting answer letters; the marker uses
the form-specific answer key. Missing questions in subset forms become missing
evidence rather than failures.

## Recognize, correct, then score (Score & Mastery)

1. Open **From roster scan**. Upload images/PDFs and enter the quiz ID printed on
   the roster. For legacy metadata without a form, explicitly confirm original
   bank order. The demonstration checkbox runs the workspace's printed roster.
2. Click **Recognize roster**. Machine output is saved as a pending draft with
   source hashes, form information, page-detection metadata, and debug overlays.
3. Inspect the rectified pages. Correct 0/1 cells in printed question order; use
   blanks for unknown answers. Missing pages remain unscored. Teacher corrections
   may mark a row scored only after its evidence is reviewed.
4. Enter the reviewer and approve. A separate workbook saves canonical results,
   the original scan, corrected printed-order table, and cell-by-cell changes;
   `provenance.json` records reviewer, timestamp, hashes, and bank/form identities.
   Only approved results become the workspace scoring input.

ArUco markers identify pages, not the printed quiz ID. The human ID check matters:
the recognizer cannot independently verify the identity of the photographed sheet.
Recognition has been tested on generated rosters, not calibrated on real classroom
photos. All-zero ticks on a blank demonstration roster are expected.

## Evaluate (sidebar: Evaluation)

The evaluator uses the same student responses and bank for both methods. It assigns
a seeded subset of observed answers per student to a held-out test set, keeping at
least one training response. Students with fewer than two responses are excluded
from this prediction experiment. Held-out answers do not enter either predictor.

- **Simple baseline:** average observed single-target correctness per skill. For
  predicting a question, multiply estimates of its required skills. Unmeasured
  skills use 0.5 for this explicit prediction rule. This is not a calibrated model.
- **DINA:** posterior predictive probability from the remaining answers and Q-matrix.
- **Sensitivity:** seven fixed configurations vary slip, guess, or prior one at a
  time. Controlled global values override item-specific parameters for this experiment.

Reports include Brier score, log loss (probabilities clipped to [1e-9, 1-1e-9] for
finite logs), threshold accuracy, per-response predictions, exact split assignments,
configurations, source hashes, and per-student/skill diagnostic sensitivity. The
sensitivity sheet uses full diagnostic responses and is distinct from held-out
prediction metrics. There is no fitting, automatic tuning, significance test, or
claim of out-of-school generalization. Selecting the best configuration on this
test set requires a separate untouched test set for an unbiased final comparison.

Optionally upload an independent reference XLSX with `student_idx`, `skill`,
`mastered` (0/1), and `assessment_id`. Document its source and affirm that it was
collected independently. It must not be a relabeling of the diagnostic responses.
The program validates identifiers and duplicate judgments; it cannot verify actual
independence. Skill comparisons use the full diagnostic input against the separate
criterion and report metrics on cases measured by every method, plus coverage.
The uploaded reference and source bank/results are preserved with each UI report.

CLI equivalent:

```powershell
python -m assessment.evaluation --bank bank.json --results results.xlsx --output evaluation.xlsx --seed 42
python -m assessment.evaluation --bank bank.json --results results.xlsx --output evaluation.xlsx --reference reference.xlsx --reference-source "Independent teacher interview"
```

## Follow up (sidebar: Follow-up assessment)

1. Start from current reviewed results or a previous follow-up session. The latter
   allows a chain of administrations without discarding earlier context.
2. Inspect uncertainty and suggested unanswered questions. Select bank questions
   or create a teacher-reviewed question with a **new unique item ID**. New items
   use the existing skill map. Drafts persist; existing questions cannot be changed
   while reusing their old responses.
3. Create a named session. The database freezes its bank version, baseline results,
   selected questions, model settings, creation timestamp, and optional parent.
4. Print the follow-up quiz/key, then record each student's correctness by item ID.
   Blank means not yet recorded. Identical repeated submissions are no-ops. To
   change an existing response, enable correction and supply a reason. Every change
   preserves the recorder and UTC recording time in an append-only history.
5. Inspect before/after probabilities, statuses, and evidence counts. Export the
   paired bank and canonical results with comparison and history sheets.

Each item contributes **at most once** to the current diagnostic likelihood. A later
response to the same item replaces the earlier one; it is never multiplied in as
independent evidence. New items contribute new evidence. Sessions retain previous
snapshots, so comparison history remains available. This is an explicit latest-item
policy, not longitudinal knowledge tracing; probability changes are not proof of
learning and may reflect repetition, marking corrections, or changed performance.
Record times are system UTC timestamps, not independently verified exam times.

## Checks and remaining research work

Run `python -m pytest tests/test_diagnostics.py tests/test_assessment_workflows.py`.
Tests exercise bank versioning, prerequisite rejection, shuffled form/answer-key
mapping, leakage isolation, independent reference validation, idempotent responses,
correction history, new-question alignment, PDF recognition, and UI transitions.

Before classroom claims, obtain expert-reviewed skills and questions, collect real
scan/response data, calibrate and validate diagnostic parameters, measure scan errors,
and evaluate with an independent criterion. The UI workflows make that work possible;
they do not substitute for it.
