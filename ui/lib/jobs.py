"""Queue generation and explicitly adopt verified, still-relevant outputs."""
import copy
import hashlib
import json
from pathlib import Path

import streamlit as st
from assessment import jobs
from assessment.curriculum_maps import require_approved
from lib.state import invalidate_after


def queue(w, kind, payload, files=None):
    try:
        job_id = jobs.submit(w.root, kind, payload, files=files)
        st.success(f"Saved job {job_id}. You can leave this page while it runs.")
        try:
            jobs.launch(w.root, job_id)
        except OSError as exc:
            st.warning(f"Job is saved but could not start: {exc}. Start it from Jobs & History.")
        st.page_link("pages/9_Jobs_and_History.py", label="View Jobs & History and use completed results")
        return job_id
    except (ValueError, OSError) as exc:
        st.error(f"Could not queue generation: {exc}")


def verified_result(root, job_id):
    job = jobs.get(root, job_id)
    if job["status"] != "succeeded":
        raise ValueError("Only successful jobs have usable results.")
    result = json.loads(job["result"])
    directory = jobs.folder(root, job_id)
    for name, expected in result["artifacts"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("A saved output has changed; it cannot be activated.")
    return result


def adopt(w, job_id):
    spec = jobs.request(w.root, job_id)
    result = verified_result(w.root, job_id)
    mapping = {"spec": "topic_spec", "bank": "bank", "summary": "cluster_summary_xlsx",
               "mastery": "mastery_clustered_xlsx", "paths": "cluster_paths_xlsx"}
    for name, entry in spec["inputs"].items():
        current = getattr(w, mapping[name])
        if not current or hashlib.sha256(Path(current).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("The active inputs changed. Keep this result in history and generate from the current inputs.")
    updated = copy.deepcopy(w)
    directory = jobs.folder(w.root, job_id)
    if spec["kind"] == "bank":
        require_approved(w.root, json.loads(Path(w.topic_spec).read_text(encoding="utf-8")))
        invalidate_after(updated, "bank")
        updated.bank = directory / result["bank"]
    elif spec["kind"] == "narratives":
        updated.cluster_summary_narratives_xlsx = directory / result["narratives"]
        updated.cluster_report_pdf = None
    elif spec["kind"] == "remediation":
        updated.remediation_pack_json = directory / result["pack"]
        updated.remediation_pack_pdf = None
    else:
        raise ValueError("Curriculum proposals must go through map review before activation.")
    updated.to_disk()
    with jobs.database(w.root) as con:
        jobs.event(con, job_id, "activated", f"Workspace manifest {updated._revision}")
    return updated
