"""Reproducible diagnostic comparisons without training/test leakage."""
import json
import sys
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.state import init_state, require_ws, header
from assessment.evaluation import evaluate, write_report
from assessment.contracts import read_results

st.set_page_config(page_title="Evaluation", layout="wide")
init_state()
w = require_ws()
header("Evaluate diagnostic reliability", description="Compare simple scoring with DINA on held-out answers, "
       "and examine how results change with model assumptions.")
if not w.bank or not w.results_xlsx:
    st.info("Load a bank and reviewed results before evaluation.")
    st.stop()
st.info("This is a within-student response holdout experiment. It does not establish "
        "generalization to other schools or learning gains. Synthetic data only tests the workflow.")
seed = st.number_input("Split seed", 0, 999999, 42)
fraction = st.slider("Fraction of observed answers held out per student", .1, .5, .25, .05)
st.caption("Students need at least two observed answers. Every model uses the same split. "
           "Sensitivity runs use controlled global parameters, overriding item-specific values. "
           "Do not select the best setting and claim its score is an unbiased final evaluation.")
reference_upload = st.file_uploader("Independent skill judgments (optional XLSX)", type=["xlsx"])
st.caption("Columns: student_idx, skill, mastered (0/1), assessment_id. "
           "These must come from a separate assessment or independent expert judgment.")
reference_source = st.text_area("Describe how the independent judgments were obtained")
confirmed = st.checkbox("The reference judgments were collected independently of these diagnostic responses")
if st.button("Run evaluation", type="primary"):
    try:
        if reference_upload and (not confirmed or not reference_source.strip()):
            raise ValueError("Document and confirm the independent reference source.")
        bank = json.loads(Path(w.bank).read_text(encoding="utf-8"))
        results = read_results(w.results_xlsx, bank)
        reference = pd.read_excel(reference_upload) if reference_upload else None
        with st.spinner("Evaluating held-out predictions and parameter sensitivity..."):
            report = evaluate(bank, results, seed=int(seed), holdout_fraction=float(fraction),
                              skill_reference=reference, reference_source=reference_source)
        folder = w.root / "evaluations" / str(uuid.uuid4())
        folder.mkdir(parents=True)
        write_report(report, folder / "evaluation.xlsx")
        (folder / "bank.json").write_text(json.dumps(bank, indent=2), encoding="utf-8")
        results.to_excel(folder / "source_results.xlsx", index=False)
        if reference_upload:
            (folder / "independent_reference.xlsx").write_bytes(reference_upload.getvalue())
        st.session_state[f"evaluation_{w.root}"] = str(folder / "evaluation.xlsx")
    except (ValueError, KeyError, TypeError) as exc:
        st.error(str(exc))
reports = sorted((w.root / "evaluations").glob("*/evaluation.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
if reports:
    selected = st.selectbox("Saved evaluation", reports, format_func=lambda p: p.parent.name)
    st.dataframe(pd.read_excel(selected, sheet_name="metrics"), hide_index=True)
    st.caption("Lower Brier score and log loss are better. Skill metrics use only cases measurable "
               "by every method; coverage and the excluded cases are available in the workbook.")
    with st.expander("Saved protocol"):
        st.dataframe(pd.read_excel(selected, sheet_name="metadata"), hide_index=True)
    st.download_button("Download evaluation workbook", selected.read_bytes(), file_name="evaluation.xlsx")
