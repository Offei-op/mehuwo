"""Offline analytical and end-to-end checks; no model server required."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from recognition.diagnostic_model import DinaModel, mastery_status
from recognition.scoring_layer import score_students
from mastery.clustering_layer import cluster_students
from mastery.remediation_layer import build_remediation_plan


def bank(qs):
    skills = [f"skill_{i}" for i in range(len(qs[0]))]
    items = [{"item_id": f"q{i}", "q_vector": q,
              **({"target_node": skills[q.index(1)]} if sum(q) == 1 else
                 {"target_nodes": [s for s, v in zip(skills, q) if v]})}
             for i, q in enumerate(qs)]
    return {"meta": {"node_order": skills, "prerequisites": {}},
            "single_skill_items": [it for it in items if "target_node" in it],
            "multi_skill_items": [it for it in items if "target_nodes" in it]}


def test_single_skill_bayes_matches_hand_calculation():
    model = DinaModel(bank([[1]]))
    assert model.infer([1])["probability"][0] == pytest.approx(0.9 / (0.9 + 0.25))
    assert model.infer([0])["probability"][0] == pytest.approx(0.1 / (0.1 + 0.75))
    assert mastery_status(model.infer([1])["probability"][0], 1) == "uncertain"


def test_repeated_evidence_and_no_perfect_certainty():
    model = DinaModel(bank([[1], [1]]))
    p = model.infer([1, 1])["probability"][0]
    assert p == pytest.approx(0.9 ** 2 / (0.9 ** 2 + 0.25 ** 2))
    assert 0.8 < p < 1
    assert mastery_status(p, 2) == "likely_mastered"


def test_joint_question_does_not_blame_every_skill_for_failure():
    model = DinaModel(bank([[1, 0], [0, 1], [1, 1]]))
    without = model.infer([1, np.nan, np.nan])["probability"]
    failed_joint = model.infer([1, np.nan, 0])["probability"]
    assert failed_joint[1] < failed_joint[0] < without[0]
    passed_joint = model.infer([1, np.nan, 1])["probability"]
    assert passed_joint[0] > failed_joint[0]
    assert passed_joint[1] > failed_joint[1]


def test_missing_is_not_incorrect_and_unmeasured_is_not_prior():
    model = DinaModel(bank([[1, 0], [0, 1]]))
    r = model.infer([np.nan, 1])
    assert np.isnan(r["probability"][0])
    assert r["evidence"].tolist() == [0, 1]
    assert mastery_status(r["probability"][0], 0) == "insufficient_evidence"
    assert np.isnan(model.infer([np.nan, np.nan])["probability"]).all()


def test_information_gain_prefers_unmeasured_skill():
    model = DinaModel(bank([[1, 0], [1, 0], [1, 0], [0, 1]]))
    r = model.infer([1, 1, np.nan, np.nan])
    recs = model.next_items(r)
    assert recs[0]["item_column"] == "item_4"
    assert recs[0]["expected_information_gain_bits"] > recs[1]["expected_information_gain_bits"] > 0
    assert model.next_items(model.infer([1, 1, 1, 1])) == []


@pytest.mark.parametrize("kwargs", [{"slip": 0}, {"guess": 1}, {"prior": np.nan},
                                    {"guess": 0.8, "slip": 0.3}])
def test_bad_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        DinaModel(bank([[1]]), **kwargs)


def test_item_specific_parameters_and_invalid_q():
    b = bank([[1]])
    b["single_skill_items"][0]["diagnostic_parameters"] = {"slip": 0.2, "guess": 0.1}
    assert DinaModel(b).infer([1])["probability"][0] == pytest.approx(0.8 / 0.9)
    b["single_skill_items"][0]["q_vector"] = [0]
    with pytest.raises(ValueError, match="q_vector"):
        DinaModel(b)


def write_case(tmp_path, b, data):
    bp = tmp_path / "bank.json"
    rp = tmp_path / "results.xlsx"
    bp.write_text(json.dumps(b), encoding="utf-8")
    pd.DataFrame(data).to_excel(rp, index=False)
    return bp, rp, tmp_path / "mastery.xlsx"


def test_spreadsheet_order_missing_and_metadata(tmp_path):
    bp, rp, out = write_case(tmp_path, bank([[1, 0], [0, 1]]), {
        "student_idx": [0, 1], "student_name": ["A", "B"],
        "item_2": [0, 1], "item_1": [1, 1], "scored": [1, 0],
    })
    result = score_students(rp, bp, out)
    assert result.loc[0, "skill_0"] > result.loc[0, "skill_1"]
    assert np.isnan(result.loc[1, "skill_0"])
    assert result.loc[1, "n_skills_unmeasured"] == 2
    assert set(pd.ExcelFile(out).sheet_names) == {"mastery", "diagnostics", "follow_up", "model", "item_parameters"}
    follow = pd.read_excel(out, sheet_name="follow_up")
    assert "exhausted" in follow[follow.student_idx == 0].iloc[0]["action"]
    baseline = score_students(rp, bp, tmp_path / "simple.xlsx", method="simple")
    assert baseline.loc[0, "skill_0"] == 1
    assert baseline.loc[0, "skill_1"] == 0


@pytest.mark.parametrize("bad", [2, -1, "yes", np.inf])
def test_bad_responses_rejected(tmp_path, bad):
    bp, rp, out = write_case(tmp_path, bank([[1]]), {
        "student_idx": [0], "student_name": ["A"], "item_1": [bad],
    })
    with pytest.raises((ValueError, TypeError)):
        score_students(rp, bp, out)
    assert not out.exists()


def test_bundled_run_flows_through_clustering_and_paths(tmp_path):
    root = Path(__file__).resolve().parents[1] / "data" / "runs" / "jhs2a"
    out = tmp_path / "mastery.xlsx"
    result = score_students(root / "results.xlsx", root / "bank.json", out)
    assert len(result) == 40
    clustered, summary = cluster_students(out, root / "bank.json", tmp_path / "clustered.xlsx",
                                          tmp_path / "summary.xlsx", k=3)
    assert "diagnostic_method" in summary
    assert not summary["label"].eq("all-mastered").any()
    paths = build_remediation_plan(tmp_path / "clustered.xlsx", root / "bank.json",
                                  tmp_path / "paths.xlsx", student_paths_xlsx=tmp_path / "students.xlsx")
    assert set(paths.action) <= {"assess", "reteach", "none"}
    assert (paths.action == "assess").any()


def test_uncertainty_does_not_become_reteaching(tmp_path):
    b = bank([[1]])
    bp, rp, out = write_case(tmp_path, b, {
        "student_idx": [0, 1], "student_name": ["A", "B"], "item_1": [1, 1],
    })
    mastery = score_students(rp, bp, out)
    mastery["cluster"] = 0
    mastery.to_excel(tmp_path / "clustered.xlsx", index=False)
    paths = build_remediation_plan(tmp_path / "clustered.xlsx", bp, tmp_path / "paths.xlsx")
    assert paths.action.tolist() == ["assess"]

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    from remediation_content import generate_remediation_pack
    from remediation_to_pdf import build_remediation_pdf

    class NoGeneration:
        def generate_json(self, *args, **kwargs):
            pytest.fail("Uncertain skill should not generate a teaching lesson")

    pd.DataFrame([{"cluster": 0, "size": 2, "mean_mastery": 0.78,
                   "label": "no-clear-group-gap"}]).to_excel(tmp_path / "summary.xlsx", index=False)
    pack = generate_remediation_pack(tmp_path / "paths.xlsx", tmp_path / "summary.xlsx",
                                     tmp_path / "clustered.xlsx", bp, tmp_path / "pack.json",
                                     client=NoGeneration(), markdown_dir=tmp_path / "markdown")
    assert pack["clusters"]["0"]["assessment_skills"] == ["skill_0"]
    assert pack["clusters"]["0"]["path"] == []
    pdf = build_remediation_pdf(tmp_path / "pack.json", tmp_path / "pack.pdf")
    assert pdf.read_bytes().startswith(b"%PDF")
    assert "Assess before" in (tmp_path / "markdown" / "cluster_0.md").read_text(encoding="utf-8")


def test_scoring_page_runs_and_invalidates_old_outputs(tmp_path):
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ui"))
    from lib.state import Workspace
    bp, rp, _ = write_case(tmp_path, bank([[1], [1]]), {
        "student_idx": [0, 1], "student_name": ["A", "B"],
        "item_1": [1, 0], "item_2": [0, 0],
    })
    w = Workspace(name="Test", root=tmp_path, created_at="2026-11-10",
                  bank=bp, results_xlsx=rp, cluster_paths_xlsx=tmp_path / "old.xlsx")
    app = AppTest.from_file(str(root / "ui/pages/3_Score_and_Mastery.py"), default_timeout=20)
    app.session_state["_initialised"] = True
    app.session_state["workspace"] = w
    app.run()
    assert not app.exception
    next(b for b in app.button if b.label == "Run scoring").click().run()
    assert not app.exception
    assert (tmp_path / "mastery.xlsx").exists()
    assert app.session_state["workspace"].cluster_paths_xlsx is None
    assert any(s.label == "Inspect student" for s in app.selectbox)


def test_identical_profiles_do_not_create_empty_clusters(tmp_path):
    bp, rp, out = write_case(tmp_path, bank([[1], [1]]), {
        "student_idx": [0, 1, 2, 3], "student_name": ["A", "B", "C", "D"],
        "item_1": [1, 1, 1, 1], "item_2": [1, 1, 1, 1],
    })
    score_students(rp, bp, out)
    _, summary = cluster_students(out, bp, tmp_path / "clustered.xlsx",
                                  tmp_path / "summary.xlsx", k=3, debug_dir=tmp_path / "debug")
    assert summary["size"].tolist() == [4]
    assert (tmp_path / "debug/heatmap.png").exists()
