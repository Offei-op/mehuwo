"""Subprocess wrappers around the mehuwo CLI scripts.

We deliberately call the CLI scripts as subprocesses rather than
importing them. Reasons:

  - The scripts already work, are documented, and are how the rest of
    the team uses the project. Wrapping is a thin layer; reimplementing
    is a maintenance liability.
  - Long LLM-driven steps stream stdout we want to display live.
  - A crash in bank_generator doesn't bring down the Streamlit server.

Each `run_*` function returns a generator that yields (stream, line)
tuples so the caller can display a live log. The yielded stream is
"stdout" or "stderr"; the final yielded tuple is ("exit", "<code>").
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from .state import REPO_ROOT


# --- Generic runner -------------------------------------------------------

def stream_command(
    cmd: list[str],
    cwd: Path | None = None,
    env_extra: dict | None = None,
) -> Iterator[tuple[str, str]]:
    """Run `cmd`, yield (stream, line) tuples line-by-line, end with
    ("exit", "<returncode>"). Lines have trailing newlines stripped."""

    env = os.environ.copy()
    # The CLI scripts inside agent/ import each other as siblings
    # (e.g. `from llm_client import ...`). Add agent/ to PYTHONPATH so
    # the imports work no matter where we cwd to.
    pp = env.get("PYTHONPATH", "")
    agent_dir = str(REPO_ROOT / "agent")
    env["PYTHONPATH"] = f"{agent_dir}{os.pathsep}{pp}" if pp else agent_dir
    if env_extra:
        env.update(env_extra)

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # merge so order is preserved
        bufsize=1,
        text=True,
    )

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield ("stdout", line.rstrip("\n"))
    finally:
        rc = proc.wait()
        yield ("exit", str(rc))


def _py(script: Path) -> list[str]:
    """Build the python invocation prefix for a given script path."""
    return [sys.executable, str(script)]


# --- Pipeline-step wrappers -----------------------------------------------

def run_bank_generator(
    spec: Path,
    output: Path,
    *,
    target_per_skill: int = 2,
    target_multi: int = 6,
    max_iterations: int = 200,
    model: str | None = None,
    host: str | None = None,
    thinking: bool | None = None,
) -> Iterator[tuple[str, str]]:
    """agent/bank_generator.py — the longest-running step. Streams
    item-by-item progress so the UI can show a live log."""
    cmd = _py(REPO_ROOT / "agent" / "bank_generator.py") + [
        "--spec", str(spec),
        "--output", str(output),
        "--target-per-skill", str(target_per_skill),
        "--target-multi", str(target_multi),
        "--max-iterations", str(max_iterations),
    ]
    if model:        cmd += ["--model", model]
    if host:         cmd += ["--host", host]
    if thinking is True:  cmd += ["--thinking"]
    if thinking is False: cmd += ["--no-thinking"]
    yield from stream_command(cmd)


def run_validate_bank(bank: Path) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "curriculum" / "validate_bank.py") + [str(bank)]
    yield from stream_command(cmd)


def run_make_quiz_pdf(
    bank: Path,
    output: Path,
    key_output: Path,
    *,
    shuffle_items: bool = False,
    shuffle_options: bool = False,
    seed: int | None = None,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "curriculum" / "make_quiz_pdf.py") + [
        str(bank),
        "--output", str(output),
        "--key-output", str(key_output),
    ]
    if shuffle_items:   cmd.append("--shuffle-items")
    if shuffle_options: cmd.append("--shuffle-options")
    if seed is not None: cmd += ["--seed", str(seed)]
    yield from stream_command(cmd)


def run_recognition(
    cells: Path, scans: list[Path], output: Path,
    *,
    debug_dir: Path | None = None,
    fill_threshold: float | None = None,
    margin_frac: float | None = None,
    px_per_mm: float | None = None,
    pdf_scale: float | None = None,
    expect_quiz_id: str | None = None,
) -> Iterator[tuple[str, str]]:
    """recognition/recognition_pipeline.py — turn scanned roster sheets into
    results.xlsx. `scans` is one or more image/PDF paths; the pipeline self-
    sorts pages by ArUco fiducial IDs so order doesn't matter. Pass the
    workspace's quiz_id via `expect_quiz_id` to guard against running this
    against the wrong cells.json (the deterministic id printed on every page
    is the metadata-misuse fence)."""
    cmd = _py(REPO_ROOT / "recognition" / "recognition_pipeline.py") + [
        "--cells", str(cells),
        "--scans", *[str(s) for s in scans],
        "--output", str(output),
    ]
    if debug_dir:                  cmd += ["--debug-dir", str(debug_dir)]
    if fill_threshold is not None: cmd += ["--fill-threshold", str(fill_threshold)]
    if margin_frac is not None:    cmd += ["--margin-frac", str(margin_frac)]
    if px_per_mm is not None:      cmd += ["--px-per-mm", str(px_per_mm)]
    if pdf_scale is not None:      cmd += ["--pdf-scale", str(pdf_scale)]
    if expect_quiz_id:             cmd += ["--expect-quiz-id", expect_quiz_id]
    yield from stream_command(cmd)


def run_scoring(
    results: Path, bank: Path, output: Path,
    *, audit: Path | None = None,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "recognition" / "scoring_layer.py") + [
        "--results", str(results),
        "--bank", str(bank),
        "--output", str(output),
    ]
    if audit: cmd += ["--audit", str(audit)]
    yield from stream_command(cmd)


def run_clustering(
    mastery: Path, bank: Path, output: Path, summary: Path,
    *, k: int = 3, debug_dir: Path | None = None,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "mastery" / "clustering_layer.py") + [
        "--mastery", str(mastery),
        "--bank", str(bank),
        "--output", str(output),
        "--summary", str(summary),
        "--k", str(k),
    ]
    if debug_dir: cmd += ["--debug-dir", str(debug_dir)]
    yield from stream_command(cmd)


def run_remediation_layer(
    mastery_clustered: Path, bank: Path, cluster_paths: Path,
    *, student_paths: Path | None = None, partial_threshold: float = 0.5,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "mastery" / "remediation_layer.py") + [
        "--mastery-clustered", str(mastery_clustered),
        "--bank", str(bank),
        "--cluster-paths", str(cluster_paths),
        "--partial-threshold", str(partial_threshold),
    ]
    if student_paths: cmd += ["--student-paths", str(student_paths)]
    yield from stream_command(cmd)


def run_cluster_narrative(
    cluster_summary: Path, bank: Path, output: Path,
    *, model: str | None = None, host: str | None = None,
    mastery_clustered: Path | None = None,
    student_output: Path | None = None,
    only_clusters: str | None = None,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "agent" / "cluster_narrative.py") + [
        "--cluster-summary", str(cluster_summary),
        "--bank", str(bank),
        "--output", str(output),
    ]
    if model: cmd += ["--model", model]
    if host:  cmd += ["--host", host]
    if mastery_clustered: cmd += ["--mastery-clustered", str(mastery_clustered)]
    if student_output:    cmd += ["--student-output", str(student_output)]
    if only_clusters:     cmd += ["--only-clusters", only_clusters]
    yield from stream_command(cmd)


def run_remediation_content(
    cluster_paths: Path, cluster_summary: Path,
    mastery_clustered: Path, bank: Path, output: Path,
    *, markdown_dir: Path | None = None, pdf: Path | None = None,
    resume: bool = False, model: str | None = None, host: str | None = None,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "agent" / "remediation_content.py") + [
        "--cluster-paths", str(cluster_paths),
        "--cluster-summary", str(cluster_summary),
        "--mastery-clustered", str(mastery_clustered),
        "--bank", str(bank),
        "--output", str(output),
    ]
    if markdown_dir: cmd += ["--markdown-dir", str(markdown_dir)]
    if pdf:          cmd += ["--pdf", str(pdf)]
    if resume:       cmd.append("--resume")
    if model:        cmd += ["--model", model]
    if host:         cmd += ["--host", host]
    yield from stream_command(cmd)


def run_cluster_report_pdf(
    cluster_summary: Path, bank: Path, output: Path,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "agent" / "cluster_report_pdf.py") + [
        "--cluster-summary", str(cluster_summary),
        "--bank", str(bank),
        "--output", str(output),
    ]
    yield from stream_command(cmd)


def run_remediation_to_pdf(
    pack: Path, output: Path, *, split: bool = False,
    output_dir: Path | None = None,
) -> Iterator[tuple[str, str]]:
    cmd = _py(REPO_ROOT / "agent" / "remediation_to_pdf.py") + [
        "--pack", str(pack),
    ]
    if split and output_dir:
        cmd += ["--output-dir", str(output_dir), "--split"]
    else:
        cmd += ["--output", str(output)]
    yield from stream_command(cmd)


# --- Ollama health check --------------------------------------------------

def ollama_reachable(host: str = "http://localhost:11434",
                     timeout: float = 1.5) -> tuple[bool, str]:
    """Quick test that the Ollama daemon is up. Returns (ok, detail)."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as r:
            if r.status == 200:
                import json as _json
                body = _json.loads(r.read())
                models = [m.get("name", "?") for m in body.get("models", [])]
                return True, f"Found {len(models)} model(s): {', '.join(models[:6])}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return False, "Unexpected response"
