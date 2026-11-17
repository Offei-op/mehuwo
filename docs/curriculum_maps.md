# Curriculum documents to reviewed skill maps

On **Topic & Items**, the teacher can now start from a curriculum document instead
of writing a skill-map JSON file. A local Ollama model proposes assessable skills
and prerequisite relationships. Its output is a **draft**, never an approval.

## Teacher workflow

1. Upload a text-based PDF, DOCX, UTF-8 TXT, or Markdown document and click **Read
   curriculum**. The source document and extracted text are saved in the workspace.
2. Select the pages or paragraphs covering the unit, enter its topic, and inspect
   the extracted text. Only the selected sections are sent to the local model.
3. Click **Propose skill map with AI**. Choose a unit small enough for 1–16 skills.
4. Inspect the skills and prerequisite table. **Explicit** means the model claims
   the curriculum supports it; **inferred** means it is a pedagogical suggestion.
   Review the quoted passage and rationale, especially for every inferred edge.
5. Correct descriptions, relationships, evidence, learner level, or classroom
   context. Save changes as a new draft. Invalid edges, duplicate skills, cycles,
   missing evidence for explicit claims, and quotations absent from the selected
   text are rejected. Inferred prerequisites need a rationale even without a quote.
6. In the separate review form, enter the reviewer name and rationale. Choose
   **approved**, **needs_revision**, or **rejected**. Approval requires confirming
   that the saved skills, evidence, and inferred relationships have been reviewed.
7. Click **Use approved map for question generation**. This explicitly activates
   the approved version; generating questions can now proceed in the Item bank tab.

The decision applies to the saved map displayed below the editor, not unsaved
edits. A new proposal does not replace a previously active map automatically.
Activating a different approved map clears incompatible active-bank and downstream
artifact pointers while preserving their files and audit history.

## Evidence and limits

PDF references use physical PDF page numbers, not necessarily the printed page
labels. DOCX references use body paragraph numbers (including table paragraphs),
not invented page numbers. DOCX headers, footers, and images are not extracted.
Text documents are grouped into 40-line sections. Check the extracted text before
requesting a proposal. Image-only PDFs use the Scanned PDF transcription panel: run OCR or transcribe manually, then review every selected page before saving the text.

Uploads are limited to 20 MB, PDF documents to 250 pages, and selected proposal
input to 18,000 text characters. Oversized selections are rejected rather than
silently truncated. Local-model quality and performance depend on the model and
hardware; no live-model pedagogical accuracy claim is made.

Quotation validation only checks that the cited wording occurs in the selected
section, allowing whitespace differences. It does **not** establish that the quote
supports the proposed skill or implies a prerequisite. Curriculum order is not
proof of a prerequisite. The reviewer must judge these relationships. The current
assessment and arithmetic-verification pipeline remains mathematics-focused.

## Approval enforcement and provenance

The workspace `assessment.sqlite` contains curriculum source records, immutable
topic-map snapshots, proposal evidence, model name, selected sections, scope,
parent versions for edited proposals, and timestamped reviewer decisions.
Source bytes are also preserved under `curriculum_sources/`. Activated maps are
exported to `topic_versions/<content-hash>.json`.

Each decision applies to an exact content version, including the evidence and
source selection for a document-derived map. Changes do not inherit approval.
The latest decision governs whether that version can be used; a reviewer can
withdraw approval by recording `needs_revision` or `rejected`.

The generator checks approval before calling the model and again before writing
the resulting bank. No JSON `approved` flag supplied by an LLM or uploaded file
can replace the local review record. Document content is passed as source data,
not instructions; the proposal model has no tool to approve or activate a map.

Bundled and manually uploaded topic maps also require an explicit reviewer
decision before **new LLM question generation**. Existing banks remain usable;
their expert bank-review workflow remains separate. Historical topic specs are
registered as drafts when viewed, never silently approved.

Reviewer identities are self-reported. This local application does not provide
authentication or protect against someone directly modifying the database or code;
the gate enforces the application workflow, not institutional authorization.

For CLI generation, first approve the exact spec through the workspace UI, then:

```powershell
python agent/bank_generator.py --spec data/runs/my_class/topic_versions/APPROVED_HASH.json --review-root data/runs/my_class --output data/runs/my_class/bank.json
```

Python callers likewise pass `review_root` to `generate_bank`. There is no
`--skip-review` option. A changed topic spec or withdrawn decision is rejected.

## Tests

`python -m pytest` includes text/PDF/DOCX extraction, evidence checks, draft-only
model output, graph validation, version-specific approval, revocation during
generation, selection limits, and the complete proposal/review/activation UI flow.
Model responses are mocked in offline tests. A real curriculum and local model
still require an expert to evaluate the usefulness and validity of the proposal.

## Background generation

**Propose skill map with AI** now saves a background job. Open **Jobs & History**, refresh to see its status, and choose **Review proposed map** after completion. The proposal remains a draft until a reviewer approves its exact version. Run inputs, source version, model exchanges and failures are retained; see [engineering records](engineering_records.md).

## OCR and validation studies (0.4.0)

Scanned PDFs can now be transcribed with local Tesseract or manually. Every selected page must be reviewed against its image before the text becomes a curriculum source. Corrections create immutable extraction versions; skill maps retain both the original document hash and extraction version. **Validation Studies** adds independent reference-map benchmarks and frozen classroom pilot protocols. See [the workflow and required inputs](validation_studies.md).
