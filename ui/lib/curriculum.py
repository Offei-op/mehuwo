"""Curriculum upload -> editable proposal -> explicit reviewer decision."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from assessment.contracts import digest
from assessment.curriculum_maps import (save_source, sources, source_sections, propose, maps,
    get_map, register_spec, save_proposal, decide, review_state, review_history, require_approved)
from lib.state import BUNDLED, invalidate_after, save_ws


def render_topic_workflow(w):
    selection_key = f"map_selection_{w.root}"
    pending_key = f"next_map_{w.root}"
    st.subheader("Start from your curriculum")
    st.write("Upload a mathematics curriculum and choose a unit. AI proposes a map; "
             "a teacher or curriculum expert must review it before question generation.")
    from lib.curriculum_ocr import render_ocr
    render_ocr(w)
    uploaded = st.file_uploader("Curriculum document", type=["pdf", "docx", "txt", "md"])
    st.caption("Text-based PDFs, DOCX, and UTF-8 text are supported. Use Scanned PDF transcription above for image-only pages. "
               "PDF references use physical page numbers; DOCX references use paragraph numbers.")
    if st.button("Read curriculum", disabled=uploaded is None):
        try:
            source_id = save_source(w.root, uploaded.getvalue(), uploaded.name)
            st.success("Document saved. Choose its pages or paragraphs below.")
        except Exception as exc:
            st.error(f"Could not read curriculum: {exc}")
    documents = sources(w.root)
    if documents:
        lookup = {d["source_id"]: d for d in documents}
        source_id = st.selectbox("Saved curriculum", list(lookup),
                                 format_func=lambda v: f"{lookup[v]['filename']} ({v[:8]})")
        sections = source_sections(w.root, source_id)
        labels = {s["id"]: s["label"] for s in sections}
        selected = st.multiselect("Pages or paragraphs for this unit", list(labels),
                                  format_func=labels.get, key=f"sections_{source_id}")
        scope = st.text_input("Topic or unit to assess", placeholder="For example: counting, addition and subtraction")
        with st.expander("Check the extracted source text", expanded=True):
            for section in sections:
                if section["id"] in selected:
                    st.markdown(f"**{section['label']}**")
                    st.text(section["text"] or "No readable text on this page.")
        model = st.text_input("Local model for proposing the map", st.session_state.get("ollama_model", "gemma4:e4b"))
        if st.button("Propose skill map with AI", disabled=not selected or not scope.strip()):
            from lib.jobs import queue
            queue(w, "curriculum", {
                "model": model, "host": st.session_state.get("ollama_host", "http://localhost:11434"),
                "source_id": source_id, "selected_sections": selected, "scope": scope,
                "source_sections_sha256": digest(sections),
            })

    with st.expander("Use an existing skill map instead"):
        st.caption("Bundled and uploaded maps also need an explicit reviewer decision before new question generation.")
        if st.button("Load bundled fractions map for review"):
            version = register_spec(w.root, json.loads(BUNDLED["topic_spec"].read_text(encoding="utf-8")))
            st.session_state[pending_key] = version
            st.rerun()
        upload_spec = st.file_uploader("Existing topic-spec JSON", type=["json"])
        if st.button("Load uploaded map for review", disabled=upload_spec is None):
            try:
                version = register_spec(w.root, json.loads(upload_spec.getvalue()))
                st.session_state[pending_key] = version
                st.rerun()
            except (ValueError, TypeError, KeyError) as exc:
                st.error(str(exc))

    # Existing workspaces migrate to explicit approval without silently approving them.
    if w.topic_spec and Path(w.topic_spec).exists():
        try:
            active_spec = json.loads(Path(w.topic_spec).read_text(encoding="utf-8"))
            register_spec(w.root, active_spec)
            active_state = review_state(w.root, digest(active_spec))["decision"]
            st.caption(f"Active topic map: {digest(active_spec)[:12]} / {active_state}")
        except (ValueError, TypeError, KeyError) as exc:
            st.error(f"Existing topic map needs repair: {exc}")
    records = maps(w.root)
    if not records:
        return
    versions = {r["version"]: r for r in records}
    if pending_key in st.session_state:
        st.session_state[selection_key] = st.session_state.pop(pending_key)
    st.subheader("Review the proposed map")
    version = st.selectbox("Map version", list(versions), key=selection_key,
                           format_func=lambda v: f"{versions[v]['scope']} ({v[:12]})")
    record = get_map(w.root, version)
    spec = json.loads(record["spec"])
    state = review_state(w.root, version)
    st.write(f"Review status: **{state['decision']}**")
    st.caption("Names are self-reported in this local application. Approval means the named "
               "reviewer accepted this exact version, not that the model validated it.")

    if record["proposal"]:
        proposal = json.loads(record["proposal"])
        st.info("Explicit = claimed support in the document. Inferred = a proposed teaching relationship. "
                "Quote matching checks the text exists; it does not prove the claim. Check both kinds before approval.")
        selected_sources = set(json.loads(record["selected_sections"]))
        with st.expander("Original curriculum excerpts for this proposal"):
            for section in source_sections(w.root, record["source_id"]):
                if section["id"] in selected_sources:
                    st.markdown(f"**{section['label']} ({section['id']})**")
                    st.text(section["text"])
        with st.form(f"edit_proposal_{version}"):
            subject = st.text_input("Subject", proposal["subject"])
            level = st.text_input("Learner level / curriculum context", proposal["learner_level"])
            context = st.text_area("Classroom context (optional)", proposal.get("classroom_context", ""))
            st.markdown("**Skills** — edit, add, or remove rows")
            skill_table = st.data_editor(pd.DataFrame(proposal["skills"]), num_rows="dynamic",
                                        column_config={"basis": st.column_config.SelectboxColumn("Evidence type", options=["explicit", "inferred"]),
                                                       "source_section": "Curriculum section", "quote": "Quoted evidence",
                                                       "rationale": "Reason", "skill_id": "Skill ID", "description": "What the student can do"})
            st.markdown("**Prerequisites** — prerequisite must come before skill")
            edge_columns = ["prerequisite", "skill", "basis", "source_section", "quote", "rationale"]
            edge_table = st.data_editor(pd.DataFrame(proposal["prerequisites"], columns=edge_columns), num_rows="dynamic",
                                       column_config={"basis": st.column_config.SelectboxColumn("Evidence type", options=["explicit", "inferred"]),
                                                      "source_section": "Curriculum section", "quote": "Quoted evidence",
                                                      "rationale": "Reason", "prerequisite": "Learn first", "skill": "Then learn"})
            limitations = st.text_area("Limitations or missing evidence (one per line)", "\n".join(proposal["limitations"]))
            save_edit = st.form_submit_button("Save edits as a new draft")
        if save_edit:
            try:
                updated = {"subject": subject, "learner_level": level, "classroom_context": context,
                           "skills": skill_table.fillna("").to_dict("records"),
                           "prerequisites": edge_table.fillna("").to_dict("records"),
                           "limitations": [line for line in limitations.splitlines() if line.strip()]}
                new_version = save_proposal(w.root, updated, source_id=record["source_id"],
                                            selected_sections=json.loads(record["selected_sections"]),
                                            scope=record["scope"], model=record["model"], parent_version=version)
                st.session_state[pending_key] = new_version
                st.rerun()
            except (ValueError, TypeError, KeyError) as exc:
                st.error(str(exc))
    else:
        st.info("This map was supplied directly. There are no linked curriculum excerpts; "
                "the reviewer must independently check its content and prerequisites.")
        with st.form(f"edit_spec_{version}"):
            edited_json = st.text_area("Topic specification", json.dumps(spec, indent=2, ensure_ascii=False), height=320)
            if st.form_submit_button("Save specification edits as a new draft"):
                try:
                    new_version = register_spec(w.root, json.loads(edited_json))
                    st.session_state[pending_key] = new_version
                    st.rerun()
                except (ValueError, TypeError, KeyError) as exc:
                    st.error(str(exc))

    st.markdown("**Saved map being reviewed**")
    for skill in spec["node_order"]:
        parents = spec["prerequisites"].get(skill, [])
        st.write(f"{skill}: {spec.get('skill_descriptions', {}).get(skill, '')}")
        st.caption("Requires: " + (", ".join(parents) if parents else "No prerequisite specified"))
    st.caption("Save edits first. The decision below applies to the saved version shown above, not unsaved edits.")
    with st.form(f"decision_{version}"):
        reviewer = st.text_input("Map reviewer name")
        rationale = st.text_area("Review rationale")
        decision = st.selectbox("Map decision", ["needs_revision", "approved", "rejected"])
        confirmed = st.checkbox("I reviewed the saved skills, source evidence, and all inferred prerequisites")
        save_decision = st.form_submit_button("Record map review decision")
    if save_decision:
        try:
            decide(w.root, version, reviewer=reviewer, rationale=rationale, decision=decision, confirmed=confirmed)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if st.button("Use approved map for question generation", disabled=state["decision"] != "approved"):
        try:
            require_approved(w.root, spec)
            folder = w.root / "topic_versions"
            folder.mkdir(exist_ok=True)
            target = folder / f"{version}.json"
            if not w.topic_spec or digest(json.loads(Path(w.topic_spec).read_text(encoding="utf-8"))) != version:
                invalidate_after(w, "bank")
                w.bank = None
            target.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
            w.topic_spec = target
            save_ws()
            st.rerun()
        except (ValueError, KeyError, TypeError) as exc:
            st.error(str(exc))
    with st.expander("Map review history"):
        st.dataframe(review_history(w.root, version), hide_index=True)
    st.download_button("Download saved map and evidence", json.dumps(record, indent=2, ensure_ascii=False),
                       file_name=f"skill_map_{version[:12]}.json", mime="application/json")
