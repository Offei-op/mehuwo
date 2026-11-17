"""Expert review of question answers, required skills, and prerequisite edges."""
import copy
import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.state import init_state, require_ws, header, save_ws, invalidate_after
from assessment.contracts import answer_options, digest, items, validate_bank
from assessment.review import history, record_review, review_status, revise_question

st.set_page_config(page_title="Expert Review", layout="wide")
init_state()
w = require_ws()
header("Expert review", description="Review the skill map and questions before classroom use. "
       "Approvals belong to an exact bank version; changes require renewed review.")
if not w.bank or not Path(w.bank).exists():
    st.info("Load or generate a question bank on Topic & Items first.")
    st.stop()
bank = json.loads(Path(w.bank).read_text(encoding="utf-8"))
try:
    validate_bank(bank)
except (ValueError, TypeError, KeyError) as exc:
    st.error(f"Repair the uploaded bank structure first: {exc}")
    st.stop()
version = digest(bank)
st.caption(f"Current bank version: {version}")
st.dataframe(review_status(w.root, bank), hide_index=True)
st.caption("Reviewer names are self-reported in this single-machine tool; there is no authenticated expert role.")
reviewer = st.text_input("Reviewer name")
scope = st.selectbox("Review target", ["skill_map", *[it["item_id"] for it in items(bank)]])
with st.form(f"review_{version}_{scope}"):
    revised = copy.deepcopy(bank)
    if scope == "skill_map":
        st.write("Skills", bank["meta"]["node_order"])
        graph = st.text_area("Prerequisite relationships (JSON)",
                             json.dumps(bank["meta"].get("prerequisites", {}), indent=2), height=260)
        st.caption("Each key is a skill; its list contains prerequisite skills. Cycles and unknown skills are rejected.")
    else:
        question = next(it for it in items(bank) if it["item_id"] == scope)
        options, correct = answer_options(question)
        stem = st.text_area("Question", question["stem"])
        edited_options = [st.text_input(f"Option {letter}", options[i]) for i, letter in enumerate("ABCD")]
        answer = st.selectbox("Correct option", list("ABCD"), index=correct)
        required = st.multiselect("Required skills", bank["meta"]["node_order"],
                                  default=[s for s, q in zip(bank["meta"]["node_order"], question["q_vector"]) if q])
        expression = st.text_input("Solution expression (optional)", question.get("solution_expression", ""))
        apply_edits = st.checkbox("Apply these question edits to a new bank version")
        st.caption("Review wording, answer, and skills together. This expert decision is not an automatic arithmetic proof.")
    decision = st.selectbox("Decision", ["approved", "needs_revision"])
    rationale = st.text_area("Reason and evidence for the decision")
    submitted = st.form_submit_button("Save review decision")
if submitted:
    try:
        if scope == "skill_map":
            revised["meta"]["prerequisites"] = json.loads(graph)
        elif apply_edits:
            revised = revise_question(bank, scope, stem=stem, options=edited_options,
                                      correct_option=answer, required_skills=required,
                                      solution_expression=expression)
        new_version = record_review(w.root, bank, revised, scope=scope, reviewer=reviewer,
                                    decision=decision, rationale=rationale)
        if new_version != version:
            folder = w.root / "bank_versions"
            folder.mkdir(exist_ok=True)
            target = folder / f"{new_version}.json"
            target.write_text(json.dumps(revised, indent=2, ensure_ascii=False), encoding="utf-8")
            invalidate_after(w, "bank")
            w.bank = target
            save_ws()
        st.success("Versioned review saved.")
        st.rerun()
    except (ValueError, KeyError, TypeError) as exc:
        st.error(str(exc))
with st.expander("Review history"):
    records = history(w.root)
    st.dataframe(records, hide_index=True)
    st.download_button("Download review history", json.dumps(records, indent=2),
                       file_name="expert_reviews.json", mime="application/json")
st.download_button("Download current bank version", json.dumps(bank, indent=2, ensure_ascii=False),
                   file_name=f"bank_{version[:12]}.json", mime="application/json")
