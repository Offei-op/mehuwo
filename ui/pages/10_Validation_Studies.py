"""Independent curriculum benchmarks and prospective classroom pilot setup."""
import io
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.state import init_state, require_ws, header
from assessment.curriculum_maps import sources, source_sections, maps, get_map
from assessment.contracts import read_results
from assessment.validation_studies import create_case, benchmark, claims, records, load, create_protocol, run_pilot

st.set_page_config(page_title="Validation Studies", layout="wide")
init_state()
w = require_ws()
header("Validation Studies", description="Record independent expert references and freeze classroom protocols before collecting outcomes.")
st.caption("These workflows organize validation evidence. Completing a form does not establish accuracy or learning gains.")
bench_tab, pilot_tab = st.tabs(["Curriculum benchmark", "Classroom pilot"])

with bench_tab:
    with st.expander("1. Freeze an independent reference case", expanded=True):
        documents = sources(w.root)
        if documents:
            source_id = st.selectbox("Benchmark curriculum", [s["source_id"] for s in documents],
                                     format_func=lambda v: next(s["filename"] + " · " + v[:8] for s in documents if s["source_id"] == v))
            sections = source_sections(w.root, source_id)
            selected = st.multiselect("Benchmark sections", [s["id"] for s in sections])
            for section in sections:
                if section["id"] in selected:
                    st.text(section["label"] + "\n" + section["text"])
            scope = st.text_input("Benchmark unit (use the same wording when proposing a map)")
            split = st.selectbox("Benchmark split", ["development", "evaluation"])
            st.caption("Use development cases to refine prompts. Keep evaluation cases aside for the final comparison. Prepare the reference before seeing model proposals.")
            reference = st.text_area("Expert reference map JSON", value='{"subject":"Mathematics","node_order":[],"prerequisites":{}}', height=160)
            reviewer = st.text_input("Reference expert")
            independent = st.checkbox("The reference was prepared independently of model proposals")
            if st.button("Freeze benchmark case"):
                try:
                    version = create_case(w.root, source_id=source_id, selected_sections=selected, scope=scope,
                                          reference=json.loads(reference), reviewer=reviewer, independent=independent, split=split)
                    st.success(f"Saved reference version {version[:12]}.")
                except (ValueError, KeyError, TypeError) as exc:
                    st.error(str(exc))
        else:
            st.info("Save a curriculum source on Topic & Items first.")
    cases = records(w.root, "benchmark_case")
    candidates = [m for m in maps(w.root) if m["proposal"]]
    if cases and candidates:
        st.subheader("2. Compare a proposal against the reference")
        case_id = st.selectbox("Reference case", [r["version"] for r in cases])
        case = load(w.root, case_id, "benchmark_case")["payload"]
        st.json(case["reference"])
        candidate_id = st.selectbox("Candidate map", [r["version"] for r in candidates])
        candidate = get_map(w.root, candidate_id)
        proposal = json.loads(candidate["proposal"])
        st.json(proposal)
        alignment = {}
        for skill in proposal["skills"]:
            alignment[skill["skill_id"]] = st.selectbox(f"Reference match for {skill['skill_id']}",
                [None] + case["reference"]["node_order"], format_func=lambda v: v or "Unmatched / extra skill",
                key=f"match_{case_id}_{candidate_id}_{skill['skill_id']}")
        evidence = {}
        for key, claim in claims(proposal).items():
            if claim["basis"] == "explicit":
                st.write(f"{key}: {claim['quote']}")
                evidence[key] = st.selectbox(f"Does the source support {key}?", ["uncertain", "supported", "unsupported"], key=f"evidence_{candidate_id}_{key}")
        scorer = st.text_input("Benchmark reviewer")
        minutes = st.number_input("Minutes spent reviewing this proposal", min_value=.1, value=1.0)
        notes = st.text_area("Corrections and alignment rationale")
        if st.button("Save benchmark review"):
            try:
                version, report = benchmark(w.root, case_id, candidate_id, alignment, evidence, reviewer=scorer, minutes=minutes, notes=notes)
                st.json(report["metrics"])
                if not report["reference_precedes_candidate"]:
                    st.warning("The reference was recorded after this candidate. Treat this as exploratory evidence, not a prospective benchmark.")
                st.success(f"Saved benchmark review {version[:12]}.")
            except (ValueError, KeyError, TypeError) as exc:
                st.error(str(exc))
    reviews = records(w.root, "benchmark_review")
    if reviews:
        st.download_button("Download benchmark review records", json.dumps(reviews, indent=2), "benchmark_reviews.json")
        st.dataframe([{"version": r["version"], **json.loads(r["payload"])["metrics"]} for r in reviews], hide_index=True)

with pilot_tab:
    st.write("Agree the assessment rubric and data-collection arrangements with the participating teacher before freezing the protocol. Use pseudonymous student IDs.")
    if w.bank:
        bank = json.loads(Path(w.bank).read_text(encoding="utf-8"))
        with st.expander("1. Freeze a pilot protocol", expanded=True):
            title = st.text_input("Pilot title")
            owner = st.text_input("Responsible teacher or researcher")
            cohort = st.text_input("Cohort description (no student names)")
            assessment_id = st.text_input("Independent assessment ID")
            item_ids = st.text_input("Independent assessment item IDs, comma-separated")
            procedure = st.text_area("Independent assessment procedure and mastery rubric", help="Explain the separate questions/tasks, scoring rubric, assessor training and how the assessor will remain unaware of diagnostic outputs.")
            independent = st.checkbox("The reference uses separate assessment evidence, not the diagnostic responses or generated diagnoses")
            seed = st.number_input("Prespecified split seed", min_value=0, value=42)
            fraction = st.slider("Prespecified held-out fraction", .1, .5, .25, .05)
            if st.button("Freeze pilot protocol"):
                try:
                    version = create_protocol(w.root, bank, title=title, owner=owner, cohort=cohort,
                        reference_assessment_id=assessment_id, reference_item_ids=[v.strip() for v in item_ids.split(",") if v.strip()],
                        reference_procedure=procedure, independent=independent, seed=int(seed), holdout_fraction=fraction)
                    st.success(f"Saved protocol {version[:12]}. Changes require a new version.")
                except (ValueError, KeyError, TypeError) as exc:
                    st.error(str(exc))
        protocols = records(w.root, "pilot_protocol")
        if protocols:
            protocol_id = st.selectbox("Frozen pilot protocol", [r["version"] for r in protocols])
            st.json(load(w.root, protocol_id, "pilot_protocol"))
            st.download_button("Download independent-assessment CSV template",
                "student_idx,skill,mastered,assessment_id,collected_at,assessor\n", "independent_assessment.csv")
            st.caption("One row per student and skill. Mastered is 0 or 1; timestamps must include a timezone. Keep assessor judgments independent of the diagnosis.")
            responses = st.file_uploader("Pilot diagnostic responses (reviewed XLSX)", type=["xlsx"])
            reference_file = st.file_uploader("Independent assessment judgments (CSV)", type=["csv"])
            diagnostic_id = st.text_input("Diagnostic assessment ID")
            collected_at = st.text_input("Diagnostic collection timestamp", placeholder="2026-11-20T10:30:00+00:00")
            blinded = st.checkbox("The reference assessor did not see diagnostic outputs before recording judgments")
            if st.button("Evaluate recorded pilot", disabled=responses is None or reference_file is None):
                try:
                    folder = run_pilot(w.root, protocol_id, bank, read_results(io.BytesIO(responses.getvalue()), bank),
                        pd.read_csv(reference_file), diagnostic_assessment_id=diagnostic_id,
                        collected_at=collected_at, assessor_blinded=blinded)
                    st.success(f"Saved pilot evaluation {folder.name[:12]}.")
                except (ValueError, KeyError, TypeError) as exc:
                    st.error(str(exc))
    else:
        st.info("Load a question bank to prepare a pilot. Its map and every question must be reviewed before pilot analysis.")
    reports = [p for p in (w.root / "pilot_evaluations").glob("*/evaluation.xlsx") if (p.parent / "manifest.json").exists()]
    if reports:
        report = st.selectbox("Pilot report", reports, format_func=lambda p: p.parent.name)
        st.dataframe(pd.read_excel(report, sheet_name="metrics"), hide_index=True)
        st.download_button("Download pilot evaluation", report.read_bytes(), "pilot_evaluation.xlsx")
