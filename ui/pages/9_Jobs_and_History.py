"""Durable generation records and explicit output activation."""
import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.state import init_state, require_ws, header, Workspace
from lib.jobs import adopt, verified_result
from assessment import jobs

st.set_page_config(page_title="Jobs & History — mehuwo", layout="wide")
init_state()
w = require_ws()
header("Jobs & History", description="Generation continues outside the browser. Review each result before using it.")
if st.button("Refresh and reload workspace"):
    if (w.root / "workspace.json").exists():
        st.session_state["workspace"] = Workspace.from_disk(w.root)
    st.rerun()
jobs.recover(w.root)
records = jobs.list_jobs(w.root)
if not records:
    st.info("Generation jobs will appear here when you propose a map or generate questions or teaching content.")
else:
    st.dataframe([{k: r[k] for k in ("id", "kind", "status", "created_at", "retry_of")} for r in records], hide_index=True)
    selected = st.selectbox("Job", [r["id"] for r in records],
                            format_func=lambda v: next(f"{r['kind']} · {r['status']} · {v[:8]}" for r in records if r["id"] == v))
    job = jobs.get(w.root, selected)
    directory = jobs.folder(w.root, selected)
    try:
        if job["error"]:
            st.error(job["error"])
        if job["status"] == "queued" and st.button("Start saved job"):
            jobs.launch(w.root, selected)
            st.rerun()
        if job["status"] in {"queued", "running"} and st.button("Cancel job"):
            jobs.cancel(w.root, selected)
            st.rerun()
        if job["status"] in {"failed", "cancelled", "interrupted"} and st.button("Retry as a new recorded attempt"):
            new_id = jobs.retry(w.root, selected)
            jobs.launch(w.root, new_id)
            st.rerun()
        if job["status"] == "succeeded":
            result = verified_result(w.root, selected)
            if job["kind"] == "ocr":
                st.info("Open Topic & Items ? Scanned PDF transcription to review this extraction before use.")
                st.page_link("pages/1_Topic_and_Items.py", label="Review curriculum transcription")
            elif job["kind"] == "curriculum":
                if st.button("Review proposed map"):
                    st.session_state[f"next_map_{w.root}"] = result["map_version"]
                    st.switch_page("pages/1_Topic_and_Items.py")
            elif st.button("Use this result", type="primary"):
                st.session_state["workspace"] = adopt(w, selected)
                st.success("Result activated. Older outputs remain in the job history.")
            for name in result["artifacts"]:
                path = directory / name
                with path.open("rb") as stream:
                    st.download_button(f"Download {path.name}", stream, file_name=path.name, key=f"output_{name}")
        with st.expander("Run records and versions"):
            st.json(jobs.request(w.root, selected))
            for name in ("request.json", "environment.json", "runtime.json", "model_calls.jsonl", "source.zip"):
                path = directory / name
                if path.exists():
                    with path.open("rb") as stream:
                        st.download_button(f"Download {name}", stream, file_name=name, key=name)
            st.dataframe(jobs.events(w.root, selected), hide_index=True)
        for name in ("worker.log", "supervisor.log"):
            path = directory / name
            if path.exists():
                with st.expander(name):
                    with path.open("rb") as stream:
                        stream.seek(max(0, path.stat().st_size - 16000))
                        st.code(stream.read().decode("utf-8", errors="replace"), language="text")
    except (ValueError, RuntimeError, OSError) as exc:
        st.error(str(exc))

st.divider()
st.subheader("Workspace backup")
st.caption("Backups include curriculum documents, reviews, responses, generated outputs and run records. Keep the ZIP in a private location.")
if st.button("Create backup"):
    from assessment.backups import create_backup
    try:
        path = create_backup(w.root)
        st.session_state[f"backup_{w.root}"] = path
    except (ValueError, RuntimeError, OSError) as exc:
        st.error(str(exc))
backup = st.session_state.get(f"backup_{w.root}")
if backup and Path(backup).exists():
    with Path(backup).open("rb") as stream:
        st.download_button("Download workspace backup", stream, file_name=Path(backup).name)
