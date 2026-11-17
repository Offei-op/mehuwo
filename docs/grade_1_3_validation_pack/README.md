# Grades 1–3 validation preparation pack

Pack version: **1.0**, 2026-11-16. Compatible with application 0.4.0.

The application remains suitable for curriculum-defined learner levels generally.
Grades 1, 2 and 3 are the initial validation scope, not a software restriction.
Mathematics remains the current project subject. Curriculum authority, country and
document language are still to be selected. No teacher/reviewer is assigned yet.

## Preparation sequence

1. Obtain the official curriculum edition for each of Grades 1–3. Record its
   authority, language, URL and relevant physical pages in `case_inventory.csv`.
2. Choose a small unit per grade from the actual documents. Candidate themes to
   look for include counting, number relationships, addition and subtraction.
   These are search themes, not claims about what a particular grade must teach.
3. Recruit a subject expert and give them `expert_review_worksheet.md` plus the
   selected curriculum pages. Keep generated proposals out of the first review.
4. Have the expert independently define assessable skills, source evidence and
   justified prerequisites. Freeze the reference in Validation Studies before
   generating a candidate for the case.
5. Use separate units for development and evaluation. Each grade has a placeholder
   for each split in the inventory. Do not fill placeholders by copying the same
   unit or adjacent overlapping excerpts across splits. Six slots are an organizing
   scaffold, not a sufficient research sample or a statistical recommendation.
6. Compare candidates with the frozen references, record corrections and review
   time, then prepare the separate assessment using `independent_assessment_plan.md`.
7. Once a teacher/classroom is available, complete the existing
   [pilot worksheet](../templates/pilot_worksheet.md) and freeze the protocol before
   collection. Use `classroom_observation_log.csv` to record practical difficulties.

The downloaded England curriculum is an extraction fixture only. It does not
select the deployment country or establish equivalence between its school years
and the intended Grades 1–3.

## Materials ready now

- `case_inventory.csv`: six unfilled planning slots for curriculum units.
- `expert_review_worksheet.md`: independent reference creation and later proposal review.
- `independent_assessment_plan.md`: separate task/rubric planning for young learners.
- `classroom_observation_log.csv`: timing, corrections, interruptions and feedback.
- [Independent judgment import template](../templates/independent_assessment.csv).

The inventory and observation log are planning files, not application imports.
Do not upload illustrative example maps as expert ground truth. No reference map,
grade-specific question bank or mastery cutoff has been approved by this pack.

## Still needed

An official curriculum link or document, including language and edition, is the
next input needed from the project owner. Reviewer and classroom recruitment can
follow after the documents and worksheets are assembled. Real OCR testing also
needs Tesseract and the appropriate language data installed.

See [validation workflows](../validation_studies.md) for application instructions.
