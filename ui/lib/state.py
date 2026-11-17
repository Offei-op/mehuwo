"""Session state and workspace path helpers.

mehuwo's pipeline runs as a sequence of CLI scripts with stable file
contracts between them. The UI's job is to keep track of:

  - Where the workspace lives (one directory per "run")
  - Which step has produced which artefact, so downstream steps know
    what to feed in and the user sees what's been done

Single source of truth is `st.session_state["ws"]`, a dict of pathlib
Paths. Pages call `init_state()` at the top, then read from `ws()`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

import streamlit as st


# Repo root (one level up from ui/)
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
UI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ROOT = REPO_ROOT / "data" / "runs"
DEMO_WORK = REPO_ROOT / "data" / "_demo"


# Bundled artefacts that ship with the repo --------------------------------

BUNDLED = {
    "topic_spec":  REPO_ROOT / "curriculum" / "Topic Specs" / "topic_spec_fractions.json",
    "bank":        REPO_ROOT / "curriculum" / "Question Bank" / "fraction_diagnostic_item_bank.json",
    "skill_graph": REPO_ROOT / "curriculum" / "Question Bank" / "Skill graph of fractions unit.png",
    "quiz":        REPO_ROOT / "curriculum" / "quiz.pdf",
    "quiz_key":    REPO_ROOT / "curriculum" / "quiz_key.pdf",
    "roster":      REPO_ROOT / "roster_test.pdf",
    "roster_cells": REPO_ROOT / "roster_test_cells.json",
}


@dataclass
class Workspace:
    """A single diagnostic run. Everything we produce for one class on
    one quiz lives under `root`. Optional paths are None until the step
    that produces them has been run."""
    name: str
    root: Path
    created_at: str

    # Inputs
    topic_spec: Path | None = None
    bank: Path | None = None

    # Materials
    quiz_pdf: Path | None = None
    quiz_key_pdf: Path | None = None
    quiz_manifest: Path | None = None
    roster_pdf: Path | None = None
    roster_cells: Path | None = None
    scan_draft: Path | None = None
    scan_context: Path | None = None

    # Results
    results_xlsx: Path | None = None
    mastery_xlsx: Path | None = None
    mastery_clustered_xlsx: Path | None = None
    cluster_summary_xlsx: Path | None = None
    cluster_paths_xlsx: Path | None = None

    # LLM-authored deliverables
    cluster_summary_narratives_xlsx: Path | None = None
    remediation_pack_json: Path | None = None
    cluster_report_pdf: Path | None = None
    remediation_pack_pdf: Path | None = None

    # Validation
    validation_report: dict | None = None

    _revision: str | None = field(default=None, repr=False)

    def to_disk(self) -> None:
        from assessment.storage import save_manifest
        manifest = {k: (str(v) if isinstance(v, Path) else v)
                    for k, v in asdict(self).items() if not k.startswith("_")}
        self._revision = save_manifest(self.root / "workspace.json", manifest, self._revision)

    @classmethod
    def from_disk(cls, root: Path) -> "Workspace":
        from assessment.storage import revision, file_lock
        root = Path(root).resolve()
        with file_lock(root / "workspace.lock"):
            manifest = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
            stamp = revision(root / "workspace.json")
        old_root = Path(manifest["root"])
        for k, v in manifest.items():
            if k in {"name", "created_at", "validation_report"} or k.startswith("_"):
                continue
            if v:
                path = Path(v)
                manifest[k] = root / path.relative_to(old_root) if path.is_relative_to(old_root) else path
        manifest["root"] = root
        manifest["_revision"] = stamp
        return cls(**manifest)


# --- Initialisation -------------------------------------------------------

def init_state() -> None:
    """Idempotent. Sets up the session keys we use across pages."""
    ss = st.session_state

    if "_initialised" in ss:
        return

    ss["_initialised"] = True
    ss["work_root"] = DEFAULT_WORK_ROOT
    ss["workspace"] = None
    ss["ollama_model"] = "gemma4:e4b"
    ss["ollama_host"] = "http://localhost:11434"

    # Inject CSS once
    css = (UI_ROOT / "styles.css").read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def ws() -> Workspace | None:
    """Return the active workspace (or None if none selected)."""
    return st.session_state.get("workspace")


def require_ws() -> Workspace:
    """Use at the top of pages that need a workspace; render a friendly
    redirect message and stop the script if there isn't one."""
    w = ws()
    if w is None:
        st.warning(
            "No workspace selected. Go back to the Home page and create one "
            "(or load the demo) before running this step."
        )
        st.stop()
    return w


def save_ws() -> None:
    """Persist the current workspace manifest to disk."""
    w = ws()
    if w is not None:
        w.to_disk()


def invalidate_after(w: Workspace, stage: str) -> None:
    """Clear pointers, retaining the old files and audit history."""
    chain = {
        "bank": ["quiz_pdf", "quiz_key_pdf", "quiz_manifest"],
        "quiz": ["roster_pdf", "roster_cells", "scan_draft", "scan_context", "results_xlsx"],
        "results": ["mastery_xlsx"],
        "mastery": ["mastery_clustered_xlsx", "cluster_summary_xlsx"],
        "clusters": ["cluster_paths_xlsx", "cluster_summary_narratives_xlsx"],
        "paths": ["remediation_pack_json", "cluster_report_pdf", "remediation_pack_pdf"],
    }
    order = list(chain)
    for name in order[order.index(stage):]:
        for attr in chain[name]:
            setattr(w, attr, None)


# --- Workspace lifecycle --------------------------------------------------

def create_workspace(name: str, work_root: Path | None = None) -> Workspace:
    """Create a new workspace directory and select it."""
    work_root = work_root or st.session_state["work_root"]
    root = work_root / _slugify(name)
    if (root / "workspace.json").exists():
        raise ValueError("A workspace with this name already exists. Open it or choose a different name.")
    root.mkdir(parents=True, exist_ok=True)
    w = Workspace(
        name=name,
        root=root,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    w.to_disk()
    st.session_state["workspace"] = w
    return w


def load_workspace(root: Path) -> Workspace:
    w = Workspace.from_disk(root)
    st.session_state["workspace"] = w
    return w


def list_workspaces(work_root: Path | None = None) -> list[Path]:
    """Existing workspaces under `work_root`. Sorted newest first."""
    work_root = work_root or st.session_state["work_root"]
    if not work_root.exists():
        return []
    paths = [p for p in work_root.iterdir() if (p / "workspace.json").exists()]
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def make_demo_workspace() -> Workspace:
    """Build a workspace that points at the repo's bundled artefacts so a
    teacher can click through the UI without running the LLM. Idempotent."""
    if (DEMO_WORK / "workspace.json").exists():
        return load_workspace(DEMO_WORK)
    DEMO_WORK.parent.mkdir(parents=True, exist_ok=True)
    w = Workspace(
        name="Demo (bundled fractions unit)",
        root=DEMO_WORK,
        created_at=datetime.now().isoformat(timespec="seconds"),
        topic_spec=BUNDLED["topic_spec"] if BUNDLED["topic_spec"].exists() else None,
        bank=BUNDLED["bank"]            if BUNDLED["bank"].exists()        else None,
        quiz_pdf=BUNDLED["quiz"]        if BUNDLED["quiz"].exists()        else None,
        quiz_key_pdf=BUNDLED["quiz_key"] if BUNDLED["quiz_key"].exists()   else None,
        roster_pdf=BUNDLED["roster"]    if BUNDLED["roster"].exists()      else None,
        roster_cells=BUNDLED["roster_cells"] if BUNDLED["roster_cells"].exists() else None,
    )
    w.to_disk()
    st.session_state["workspace"] = w
    return w


# --- Step completion ------------------------------------------------------

STEPS = [
    ("topic_spec",    "Topic spec"),
    ("bank",          "Item bank"),
    ("quiz_pdf",      "Quiz materials"),
    ("results_xlsx",  "Results uploaded"),
    ("mastery_xlsx",  "Mastery scored"),
    ("mastery_clustered_xlsx", "Students clustered"),
    ("cluster_paths_xlsx",     "Remediation path"),
    ("remediation_pack_json",  "Content authored"),
    ("remediation_pack_pdf",   "Reports printed"),
]


def step_status(w: Workspace) -> list[tuple[str, str, bool]]:
    """List of (attribute, label, done) for the progress display."""
    return [
        (attr, label, getattr(w, attr) is not None
                       and Path(getattr(w, attr)).exists())
        for attr, label in STEPS
    ]


# --- Helpers --------------------------------------------------------------

def _slugify(s: str) -> str:
    out = []
    for ch in s.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("_")
    return "".join(out) or f"workspace_{datetime.now():%Y%m%d_%H%M%S}"


def header(title: str, eyebrow: str | None = None,
           description: str | None = None) -> None:
    """Consistent page header across all pages."""
    if eyebrow:
        st.markdown(f'<div class="eyebrow">{eyebrow}</div>',
                    unsafe_allow_html=True)
    st.markdown(f"<h1>{title}</h1>", unsafe_allow_html=True)
    if description:
        st.markdown(f'<p class="lead">{description}</p>',
                    unsafe_allow_html=True)
    st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
