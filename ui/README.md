# mehuwo UI

A Streamlit interface for the mehuwo diagnostic pipeline. Wraps the existing CLI scripts (no reimplementation), keeps state across pipeline stages, and gives the teacher a workspace-per-class model for managing runs.

---

## Run

From the repo root, with the project environment activated:

```bash
pip install streamlit matplotlib   # if not already installed
streamlit run ui/app.py
```

Open the printed URL (default `http://localhost:8501`). Sidebar nav exposes the five pipeline stages.

If you intend to actually generate items or remediation content (anything LLM-driven), start Ollama in another shell first:

```bash
ollama serve
ollama pull gemma4:e4b
```

The home page surfaces a green pill when the daemon is reachable and a red one when it isn't — LLM-driven pages disable their primary buttons in the red state.

---

## Layout

```
ui/
├── app.py                          Home — workspace lifecycle + progress
├── pages/
│   ├── 1_Topic_and_Items.py        Topic spec + bank generation/validation
│   ├── 2_Materials.py              Quiz PDF + roster sheet
│   ├── 3_Score_and_Mastery.py      Upload results → mastery
│   ├── 4_Clusters_and_Path.py      Clustering + remediation path
│   └── 5_Author_and_Print.py       Narratives + content + PDFs
├── lib/
│   ├── state.py                    Session state, workspace dataclass
│   ├── runners.py                  Subprocess wrappers around CLI scripts
│   ├── viz.py                      Matplotlib charts (palette-matched)
│   └── demo.py                     Synthetic results generator
├── styles.css                      Custom CSS — typography, palette, pills
└── .streamlit/config.toml          Theme config
```

The numeric prefixes on the pages drive Streamlit's sidebar ordering. Underscores in filenames render as spaces in the sidebar.

---

## Concepts

### Workspace

A workspace is one diagnostic run for one class. It's a directory under `data/runs/<slug>/` containing every artefact the pipeline produces — bank, quiz PDFs, results, mastery, clusters, narratives, teaching packs, final PDFs — plus a `workspace.json` manifest listing the paths. Workspaces persist across Streamlit restarts; the home page lists existing ones and lets you reopen them.

The **demo workspace** lives at `data/_demo/` and points at the bundled fractions artefacts so a teacher can click through every screen without running the LLM at all.

### Subprocess wrappers, not re-imports

`lib/runners.py` calls each CLI script via `subprocess.Popen`, yielding `(stream, line)` tuples line by line for live log display. Reasons:

- The scripts already work and are how the team uses the project.
- LLM steps stream tool-call progress that the UI surfaces in real time.
- A crash in `bank_generator.py` doesn't bring down the Streamlit server.

`PYTHONPATH` includes `agent/` so the agent scripts' sibling imports work regardless of where the subprocess is launched from.

### Honest about the OCR gap

The OCR step (scanned roster sheet → `results.xlsx`) is not built yet. Page 3 has three tabs reflecting this: upload a manually-prepared file, generate synthetic results from the bank for demo purposes, or read the "not yet available" placeholder. The synthetic generator samples per-item correctness from three archetypes (struggling / developing / strong) with depth-profile-aware probabilities so the cluster structure downstream actually looks like something.

---

## Stage-by-stage flow

**1. Topic & Items.** Load a topic spec (bundled or upload), then either load the bundled bank, upload one, or generate a new one. Generation streams the agent's tool-call log live and shows running counts of verifier-accepted vs verifier-rejected items. Validation runs the same `validate_bank.py` checks the CLI uses.

**2. Materials.** Renders the quiz + key via `make_quiz_pdf.py`. Roster generation calls `recognition.score_sheet_template.generate_roster_pdf` directly (it doesn't have a CLI). Class size, names, school label, and quiz title are all editable.

**3. Score & Mastery.** Get a `results.xlsx` into the workspace (three ways), then run the scoring layer. Class-wide mastery distribution + per-student mean histogram render on completion using the same palette as the PDF reports.

**4. Clusters & Path.** Hierarchical clustering with debug-plot output, per-cluster cards with the inferred labels (foundation-gap / middle-gap / leaf-gap / broad-gap / all-mastered) shown as pills, centroid heatmap + size bar inline. Then the remediation-layer step builds the topo-ordered path per cluster.

**5. Author & Print.** Three tabs for the two LLM-authoring steps and the two PDF renderers. The content step supports `--resume` (default on) since it's the longest step in the pipeline — checkpointing after every (cluster, skill) lets you recover from a crash without losing in-flight work.

---

## Design notes

A handful of choices worth flagging for anyone extending this:

**Palette is matched between CSS and matplotlib.** `lib/viz.PALETTE` and `styles.css` use the same hex values, sourced from the project's hand-drawn skill-graph image — muted forest greens, warm amber, dusty plum on warm cream. Charts on screen look like the PDF charts; the visual hand-off is intentional.

**Forward references matter in Streamlit.** The script runs top to bottom on every interaction. Helper functions called from inside `if st.button(...)` blocks must be defined *above* the button, not below — applied this fix in `2_Materials.py` (for `_count_items`) and `5_Author_and_Print.py` (for `_render_skill_pack` and `_run_with_log`).

**Browser storage is out, session state is in.** Streamlit's `st.session_state` plus the on-disk `workspace.json` is the persistence model. No localStorage, no cookies, no auth — this is a single-machine teacher tool.

**Custom HTML for pills, cards, and eyebrows.** Streamlit's native components are visually homogeneous; the small CSS classes (`.pill`, `.card`, `.eyebrow`, `.lead`, `.kbd`, `.muted`, `.section-rule`) let the pages have a typographic hierarchy without fighting the framework. Use them; don't reinvent them.

---

## Troubleshooting

**"ModuleNotFoundError: No module named 'lib'"** — run from the repo root with `streamlit run ui/app.py`. Each page prepends `ui/` to `sys.path` so `lib.*` resolves; running pages directly won't work.

**Ollama pill shows red, but `ollama list` works.** Check the host — the UI defaults to `http://localhost:11434`. If yours runs elsewhere, set `st.session_state["ollama_host"]` (currently hard-coded; promoting this to a settings page is a five-minute change).

**Bank generation hangs / times out.** The `--no-thinking` mode is much faster on weak hardware. Try "force off" in the Thinking dropdown; flip back to "force on" only if you see `arithmetic_mismatch` errors in the log.

**Streamlit complains about widget keys.** The split-mode remediation download buttons use `key=f"dl_{p.name}"` to avoid collisions when multiple per-cluster PDFs render. Follow the same pattern if you add more dynamic download buttons.

**Demo workspace charts look empty.** The demo points at the bundled artefacts but doesn't include synthetic results. Use the "Generate synthetic" tab on Page 3 to populate downstream stages.

---

## What's not in here

These are deliberate omissions for the v1 of this UI; obvious next things if you want to extend:

- **Authentication / multi-user.** Single-machine tool.
- **A settings page** for the Ollama host, default model, and `work_root` location.
- **OCR step.** Same gap as the rest of the project — the roster sheets already carry every fiducial and cell coordinate needed; just nobody's written the rectifier yet.
- **Background job queue.** Long LLM runs block the Streamlit page they're started from. Workable for single-teacher use; would be the first thing to fix for a shared deployment.
- **Tests.** `lib/` has no test coverage. The runners are thin enough that integration testing the actual CLI scripts (which the project should be doing anyway) gets you most of the way.
