"""Teacher-selected questions, repeat administrations, and diagnostic changes."""
import json
import sys
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.state import init_state, require_ws, header
from assessment.contracts import digest, items, make_form, read_results
from assessment.followup import (sessions, load_session, create_session, expand_results,
                                session_results, record_responses, response_history, compare_session)
from assessment.review import revise_question, record_review
from recognition.diagnostic_model import DinaModel, mastery_status

st.set_page_config(page_title="Follow-up assessment", layout="wide")
init_state()
w = require_ws()
header("Follow-up assessment", description="Collect more evidence for uncertain skills and compare "
       "the diagnosis before and after. Each session keeps its bank, baseline, and response history.")
existing = sessions(w.root)
tab_new, tab_record = st.tabs(["Prepare a follow-up", "Record and compare"])
with tab_new:
    sources = ["workspace"] if w.bank and w.results_xlsx else []
    sources += [s["session_id"] for s in existing]
    titles = {s["session_id"]: s["title"] for s in existing}
    if not sources:
        st.info("Load reviewed results first.")
    else:
        source = st.selectbox("Baseline", sources, format_func=lambda s: "Current workspace results" if s == "workspace" else titles[s])
        if source == "workspace":
            source_bank = json.loads(Path(w.bank).read_text(encoding="utf-8"))
            base = read_results(w.results_xlsx, source_bank)
        else:
            _, source_bank, _ = load_session(w.root, source)
            base = session_results(w.root, source)
        key = f"followup_bank_{w.root}_{source}_{digest(source_bank)}"
        draft_file = w.root / "followup_drafts" / (digest({"source": source, "bank": digest(source_bank)}) + ".json")
        saved_draft = json.loads(draft_file.read_text(encoding="utf-8")) if draft_file.exists() else source_bank
        draft = st.session_state.get(key, saved_draft)
        skills = draft["meta"]["node_order"]
        with st.expander("Create a teacher-authored question"):
            with st.form(f"new_question_{key}"):
                new_id = st.text_input("New unique item ID", "FOLLOWUP_" + digest(source_bank)[:6])
                stem = st.text_area("Question stem")
                options = [st.text_input(f"Answer {letter}") for letter in "ABCD"]
                answer = st.selectbox("Correct answer", list("ABCD"))
                required = st.multiselect("Skills required by this question", skills)
                author = st.text_input("Author / reviewing teacher")
                rationale = st.text_area("Why this question measures the selected skills")
                add = st.form_submit_button("Add reviewed question to follow-up draft")
            if add:
                try:
                    if new_id in {it["item_id"] for it in items(draft)}:
                        raise ValueError("Use a new item ID; existing evidence must not be attached to revised questions.")
                    revised = revise_question(draft, new_id, stem=stem, options=options,
                                              correct_option=answer, required_skills=required)
                    record_review(w.root, draft, revised, scope=new_id, reviewer=author,
                                  decision="approved", rationale=rationale)
                    st.session_state[key] = revised
                    draft_file.parent.mkdir(parents=True, exist_ok=True)
                    draft_file.write_text(json.dumps(revised, indent=2, ensure_ascii=False), encoding="utf-8")
                    st.rerun()
                except (ValueError, KeyError, TypeError) as exc:
                    st.error(str(exc))
        try:
            expanded = expand_results(base, source_bank, draft)
            model = DinaModel(draft)
            student = st.selectbox("Inspect uncertainty for student", expanded.student_idx.tolist())
            row = expanded[expanded.student_idx == student].iloc[0]
            cols = [f"item_{i+1}" for i in range(len(items(draft)))]
            inference = model.infer(row[cols].to_numpy(float))
            st.dataframe([{"skill": s, "probability": p, "status": mastery_status(p, n)}
                          for s, p, n in zip(skills, inference["probability"], inference["evidence"])], hide_index=True)
            recommendations = model.next_items(inference)
            st.dataframe(recommendations, hide_index=True)
            st.caption("Suggestions use the default DINA assumptions. You can select other questions. "
                       "New questions require teacher review; the system does not generate them automatically here.")
            lookup = {it["item_id"]: it for it in items(draft)}
            selected = st.multiselect("Questions to administer", list(lookup),
                                      default=[r["item_id"] for r in recommendations],
                                      format_func=lambda iid: f"{iid}: {lookup[iid]['stem'][:90]}")
            title = st.text_input("Follow-up session name", "Follow-up assessment")
            if st.button("Create follow-up session", disabled=not selected):
                sid = create_session(w.root, draft, expanded, selected, title=title,
                                     parent_session=None if source == "workspace" else source)
                st.success(f"Session created: {sid}. Open Record and compare.")
                st.rerun()
        except (ValueError, KeyError, TypeError) as exc:
            st.error(str(exc))

with tab_record:
    if not existing:
        st.info("Create a session first.")
    else:
        labels = {s["session_id"]: f"{s['title']} ({s['created_at']})" for s in existing}
        sid = st.selectbox("Session", list(labels), format_func=labels.get)
        session, bank, base = load_session(w.root, sid)
        st.caption(f"Bank version: {session['bank_version']} | created: {session['created_at']}")
        chosen = json.loads(session["selected_items"])
        lookup = {it["item_id"]: it for it in items(bank)}
        if st.button("Prepare follow-up quiz and answer key"):
            from curriculum.make_quiz_pdf import normalize_item, build_quiz_pdf, build_answer_key_pdf
            printed = [normalize_item(lookup[iid]) for iid in chosen]
            form = make_form(bank, printed)
            folder = w.root / "followups" / sid
            folder.mkdir(parents=True, exist_ok=True)
            build_quiz_pdf(printed, bank["meta"], folder / "quiz.pdf", form_id=form["form_id"])
            build_answer_key_pdf(printed, bank["meta"], folder / "key.pdf", form_id=form["form_id"])
            (folder / "quiz.form.json").write_text(json.dumps(form, indent=2), encoding="utf-8")
        for name in ("quiz.pdf", "key.pdf", "quiz.form.json"):
            path = w.root / "followups" / sid / name
            if path.exists():
                st.download_button(f"Download {name}", path.read_bytes(), file_name=name)
        student = st.selectbox("Student to record", base.student_idx.tolist())
        records = response_history(w.root, sid)
        latest = {r["item_id"]: r["correct"] for r in records if r["student_id"] == str(student)}
        with st.form(f"responses_{sid}_{student}"):
            table = pd.DataFrame([{"item_id": iid, "question": lookup[iid]["stem"],
                                   "correct": latest.get(iid, np.nan)} for iid in chosen])
            edited = st.data_editor(table, disabled=["item_id", "question"], num_rows="fixed",
                                   column_config={"correct": st.column_config.NumberColumn("Correct (0/1)", min_value=0, max_value=1, step=1)})
            recorder = st.text_input("Recorded by")
            revise = st.checkbox("Correct previously recorded responses")
            reason = st.text_input("Correction reason (required when changing an existing response)")
            submit = st.form_submit_button("Save responses")
        if submit:
            try:
                answers = {r.item_id: r.correct for _, r in edited.iterrows() if pd.notna(r.correct)}
                n = record_responses(w.root, sid, student, answers, reviewer=recorder, revise=revise, reason=reason)
                st.success(f"Saved {n} response changes. Repeated identical submissions add no evidence.")
            except (ValueError, KeyError, TypeError) as exc:
                st.error(str(exc))
        comparison = compare_session(w.root, sid)
        st.dataframe(comparison[comparison.student_idx == student], hide_index=True)
        st.caption("Before/after uses the same model settings. For repeated questions, the latest response "
                   "replaces earlier evidence rather than being multiplied into it. A change is not proof "
                   "of learning: practice effects, time, and instruction can also matter.")
        with st.expander("Response and correction history"):
            st.dataframe(response_history(w.root, sid), hide_index=True)
        if st.button("Export session results and comparison"):
            folder = w.root / "followups" / sid
            folder.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(folder / "results.xlsx", engine="openpyxl") as writer:
                session_results(w.root, sid).to_excel(writer, sheet_name="results", index=False)
                pd.DataFrame([{"bank_version": session["bank_version"], "column_order": "canonical",
                               "session_id": sid}]).to_excel(writer, sheet_name="assessment_meta", index=False)
                comparison.to_excel(writer, sheet_name="comparison", index=False)
                pd.DataFrame(response_history(w.root, sid)).to_excel(writer, sheet_name="history", index=False)
            (folder / "bank.json").write_text(json.dumps(bank, indent=2), encoding="utf-8")
        for name in ("results.xlsx", "bank.json"):
            path = w.root / "followups" / sid / name
            if path.exists():
                st.download_button(f"Download session {name}", path.read_bytes(), file_name=name)
