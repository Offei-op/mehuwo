# Engineering records and recovery

## Version 0.3.0 — 2026-11-16

This development version adds durable generation and reproducibility records to
the curriculum approval workflow. `VERSION` identifies the application release;
`CHANGELOG.md` records changes. Neither implies a Git commit or published release.
Every job archives its exact Python source and records a content fingerprint,
including uncommitted changes. `requirements-tested.lock` captures the installed
Python 3.14.7 test environment. Optional legacy training dependencies remain in
`requirements.txt` and are not part of that tested environment.

## Teacher workflow

1. Propose a curriculum map or request questions, narratives or remediation.
2. Open **Jobs & History**. Generation runs outside the browser; refresh to see
   its latest status. One job runs at a time within a workspace.
3. For a curriculum proposal, choose **Review proposed map**. Review, approval
   and activation remain separate steps. AI cannot grant approval.
4. For other outputs, inspect/download the result and choose **Use this result**.
   If the active inputs have changed, the old result stays in history but cannot
   replace the current work. Question-bank activation rechecks map approval.
5. Cancel unwanted work or retry a failed/interrupted attempt. A retry has its
   own ID and links to the previous attempt; previous files remain intact.

Closing the browser does not cancel work. Turning off the machine stops it.
Opening Jobs & History recovers expired running leases as `interrupted` after
90 seconds without a supervisor heartbeat. Queued jobs can be started there.
Workers stop if they lose their supervisor lease. Cancellation stops the local
worker; the model server may take additional time to release its computation.

## What each run records

`jobs.sqlite` stores job status, timestamps, retry relationships and events.
`jobs/<run-id>/` contains:

| File | Record |
|---|---|
| `request.json` and `inputs/` | Input snapshots, checksums, model choice and generation settings |
| `source.zip` | Exact application and test source at submission |
| `environment.json` | Python, OS and installed package versions |
| `runtime.json` | Effective model settings, resolved model digest when available, checkpoint reuse |
| `model_calls.jsonl` | Timestamped requests, prompts, schemas, options, responses and errors |
| `worker.log`, `supervisor.log` | Execution logs |
| `outputs/`, `result.json` | Staged files and successful-output checksums |
| `error.json` | Failure details and traceback, when the worker could write them |

These records can include curriculum text and student information. They stay in
the local workspace; the configured Ollama host receives generation inputs.
Default classroom workspace directories are excluded from Git.

Question banks checkpoint verified items atomically. Compatible retries restore
and reverify item identities; incomplete coverage is a failed job, not a usable
completed bank. Remediation checkpoints each completed teaching step. Checkpoints
are reused only when source, inputs, settings and a known model digest match.
Without a resolved digest, generation restarts. Map proposals and narratives
restart from their recorded inputs rather than resuming an incomplete model call.
The system detects source or model changes during execution before publishing
results. Records support comparison and investigation, not a promise of identical
LLM output on repeated runs.

Workspace manifests use atomic replacement and optimistic conflict detection.
If another browser session changed the workspace, reload it from Jobs & History
before saving again. Saved generation files remain available.

## Backup and restore

Finish or cancel generation, and stop editing the workspace while making a backup.
Choose **Create backup** in Jobs & History and download the ZIP. It includes source
documents, review decisions, assessment records, outputs, job records and external
files referenced by the workspace (including bundled demo inputs). SQLite uses its
online backup API: each database snapshot is internally consistent. This is not
a cross-database transactional snapshot during concurrent expert editing.

Restore to a **new** directory; existing workspaces are never overwritten:

```powershell
python -m assessment.backups C:\Backups\workspace.zip data\runs\restored_class
```

Then open the restored workspace from Home. Internal workspace paths are rebased
when loaded. Restore verifies archive checksums, paths and SQLite integrity before
writing files. Current portability testing covers relocation on Windows; moving
Windows-authored manifests to another operating system is not yet supported.

## Verification and remaining work

Offline tests cover queue deduplication, immutable inputs, exclusive leases,
expired-worker fencing, cancellation races, actual subprocess failure recording,
source-change rejection, model exchange records, bank checkpoint retries, stale
output rejection, workspace conflicts, backup restoration, archive path checks,
and the proposal/review/activation UI flow. They do not require a running model.
The GitHub Actions workflow is configured; hosted execution has not been verified.

Local verification on 2026-11-16: **55 tests passed** on Windows / Python 3.14.7.
The suite reported one existing SciPy plotting warning for identical clustering
profiles. Python compilation and `git diff --check` also passed. No real-model
quality benchmark or classroom validation was performed for this development version.

Next engineering work remains:

- Benchmark real curriculum documents and expert-reviewed maps; measure evidence
  accuracy, missing skills, incorrect prerequisites and reviewer correction effort.
- Add OCR for scanned curriculum PDFs, with visible extraction corrections and
  source-page references before proposal generation.
- Test prolonged real-model runs, machine restarts and realistic classroom sizes.
- Add authenticated reviewer identities and server-wide scheduling before shared
  institutional deployment. Current reviewer names are self-reported.
- Evaluate diagnostic quality against independent assessments before claiming
  educational effectiveness. Engineering tests do not establish that validity.

## 0.4.0 follow-on engineering record ? 2026-11-16

Implemented selected-page OCR jobs and manual transcription with image/text review; independent reference cases and benchmark scoring; prospective pilot protocols and reproducible evaluation archives. See [validation study workflows](validation_studies.md). These implement the tooling portions of the next-work list above. Real expert scoring, live-engine OCR validation and classroom evidence remain outstanding. The public England primary mathematics PDF passed extraction on all 47 pages; the source catalog records its hash and URL.

0.4.0 local verification: **64 tests passed** on Windows / Python 3.14.7, including the downloaded real-document extraction fixture. One existing SciPy plotting warning remains. Compilation and `git diff --check` passed. OCR transport was mocked; Tesseract is not installed locally, and no real-model accuracy or classroom-outcome claims were tested.
