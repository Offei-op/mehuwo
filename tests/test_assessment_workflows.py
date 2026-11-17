"""Offline integration checks for pilot workflows and data provenance."""
import copy
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from assessment.contracts import digest, items, make_form, remap_results
from assessment.review import record_review, review_status, history, revise_question
from assessment.evaluation import evaluate, write_report
from assessment.followup import (create_session, record_responses, response_history,
                                compare_session, session_results, expand_results)
from assessment.scan_review import approve_scan
from curriculum.make_quiz_pdf import normalize_item, shuffle_options, main as quiz_main
from recognition.scoring_layer import score_students


@pytest.fixture
def bank():
    def question(i, q, answer="B"):
        return {"item_id": f"Q{i}", "stem": f"Question {i}: what is 1 + 1?",
                "options": dict(zip("ABCD", ["1", "2", "3", "4"])), "correct_option": answer,
                "q_vector": q, **({"target_node": "A" if q[0] else "B"} if sum(q) == 1
                                   else {"target_nodes": ["A", "B"]})}
    return {"meta": {"subject": "Test", "node_order": ["A", "B"], "prerequisites": {"A": [], "B": ["A"]}},
            "single_skill_items": [question(1, [1, 0]), question(2, [1, 0]),
                                   question(3, [0, 1]), question(4, [0, 1])],
            "multi_skill_items": [question(5, [1, 1])]}


@pytest.fixture
def results():
    return pd.DataFrame({"student_idx": [0, 1, 2], "student_name": ["One", "Two", "Three"],
                         "item_1": [1, 0, 1], "item_2": [0, 0, 1], "item_3": [1, 0, 0],
                         "item_4": [0, 1, 1], "item_5": [1, 0, 1], "scored": [1, 1, 1]})


def test_reviews_are_version_specific_and_keep_original(tmp_path, bank):
    original = copy.deepcopy(bank)
    version = record_review(tmp_path, bank, bank, scope="Q1", reviewer="Teacher",
                            decision="approved", rationale="Checked independently")
    assert next(r for r in review_status(tmp_path, bank) if r["scope"] == "Q1")["status"] == "approved"
    changed = revise_question(bank, "Q1", stem="Corrected question", options=["1", "2", "3", "4"],
                              correct_option="C", required_skills=["B"])
    record_review(tmp_path, bank, changed, scope="Q1", reviewer="Second teacher",
                  decision="needs_revision", rationale="Needs another check")
    assert bank == original
    assert digest(changed) != version
    assert len(history(tmp_path)) == 2
    assert next(r for r in review_status(tmp_path, changed) if r["scope"] == "Q1")["status"] == "needs_revision"
    cyclic = copy.deepcopy(bank)
    cyclic["meta"]["prerequisites"]["A"] = ["B"]
    with pytest.raises(ValueError, match="cycle"):
        record_review(tmp_path, bank, cyclic, scope="skill_map", reviewer="Teacher", decision="approved", rationale="Test")
    assert len(history(tmp_path)) == 2


def test_shuffle_mapping_and_option_key(tmp_path, bank, results):
    rendered = [normalize_item(it) for it in items(bank)]
    rng = random.Random(71)
    rng.shuffle(rendered)
    rendered = [shuffle_options(it, rng) for it in rendered]
    form = make_form(bank, rendered)
    printed = results[["student_idx", "student_name", "scored"]].copy()
    for j, it in enumerate(rendered):
        printed[f"item_{j+1}"] = results[f"item_{int(it['item_id'][1:])}"]
        assert it["options"]["ABCD".index(form["items"][j]["correct_letter"])] == "2"
    canonical = remap_results(printed, bank, form)
    pd.testing.assert_frame_equal(canonical[results.columns], results, check_dtype=False)
    altered = copy.deepcopy(bank)
    altered["meta"]["subject"] = "Changed"
    with pytest.raises(ValueError, match="version"):
        remap_results(printed, altered, form)
    bank_path = tmp_path / "bank.json"
    bank_path.write_text(json.dumps(bank), encoding="utf-8")
    raw_path = tmp_path / "raw.xlsx"
    with pd.ExcelWriter(raw_path) as writer:
        printed.to_excel(writer, sheet_name="results", index=False)
        pd.DataFrame([{"json": json.dumps(form)}]).to_excel(writer, sheet_name="form_manifest", index=False)
    mapped = score_students(raw_path, bank_path, tmp_path / "mapped.xlsx")
    results.to_excel(tmp_path / "canonical.xlsx", index=False)
    expected = score_students(tmp_path / "canonical.xlsx", bank_path, tmp_path / "expected.xlsx")
    pd.testing.assert_frame_equal(mapped, expected)


def test_renderer_writes_manifest_and_unique_form_ids(tmp_path, bank):
    bp = tmp_path / "bank.json"
    bp.write_text(json.dumps(bank), encoding="utf-8")
    quiz_main([str(bp), "--output", str(tmp_path / "quiz.pdf"), "--shuffle-items", "--shuffle-options", "--seed", "7"])
    manifest = json.loads((tmp_path / "quiz.form.json").read_text(encoding="utf-8"))
    assert manifest["bank_version"] == digest(bank)
    assert len(manifest["items"]) == 5
    assert (tmp_path / "quiz.pdf").read_bytes().startswith(b"%PDF")


def test_holdout_answers_do_not_change_predictions(bank, results):
    report = evaluate(bank, results, seed=19)
    changed = results.copy()
    for _, row in report["split"].query("partition == 'test'").iterrows():
        col = f"item_{row.item_index+1}"
        changed.loc[row.row, col] = 1 - changed.loc[row.row, col]
    second = evaluate(bank, changed, seed=19)
    np.testing.assert_allclose(report["predictions"].predicted, second["predictions"].predicted)
    assert not report["predictions"].actual.equals(second["predictions"].actual)
    assert len(report["metrics"]) == 8
    pd.testing.assert_frame_equal(report["split"], evaluate(bank, results, seed=19)["split"])


def test_independent_references_and_workbook(tmp_path, bank, results):
    refs = pd.DataFrame({"student_idx": [0, 1], "skill": ["A", "B"], "mastered": [1, 0],
                         "assessment_id": ["external_1", "external_1"]})
    with pytest.raises(ValueError, match="source"):
        evaluate(bank, results, skill_reference=refs)
    report = evaluate(bank, results, skill_reference=refs, reference_source="Independent teacher interview")
    assert "independent_skill_judgment" in set(report["metrics"].task)
    assert len(report["skill_judgments"]) == 16
    write_report(report, tmp_path / "evaluation.xlsx")
    assert set(pd.ExcelFile(tmp_path / "evaluation.xlsx").sheet_names) == set(report)
    refs.loc[0, "skill"] = "unknown"
    with pytest.raises(ValueError, match="absent"):
        evaluate(bank, results, skill_reference=refs, reference_source="External")


def test_followup_idempotence_corrections_and_new_questions(tmp_path, bank, results):
    expanded_bank = revise_question(bank, "NEW", stem="New independent question", options=["1", "2", "3", "4"],
                                    correct_option="B", required_skills=["A"])
    expanded = expand_results(results, bank, expanded_bank)
    sid = create_session(tmp_path, expanded_bank, expanded, ["NEW", "Q1"], title="Check A")
    assert record_responses(tmp_path, sid, 0, {"NEW": 1, "Q1": 1}, reviewer="Teacher") == 2
    assert record_responses(tmp_path, sid, 0, {"NEW": 1, "Q1": 1}, reviewer="Teacher") == 0
    with pytest.raises(ValueError, match="already exists"):
        record_responses(tmp_path, sid, 0, {"NEW": 0}, reviewer="Teacher")
    assert len(response_history(tmp_path, sid)) == 2
    comparison = compare_session(tmp_path, sid)
    skill = comparison[(comparison.student_idx == 0) & (comparison.skill == "A")].iloc[0]
    assert skill.evidence_after == skill.evidence_before + 1  # Q1 is replaced, not counted twice
    assert skill.after > skill.before
    record_responses(tmp_path, sid, 0, {"NEW": 0}, reviewer="Teacher", revise=True, reason="Marking correction")
    assert len(response_history(tmp_path, sid)) == 3
    combined = session_results(tmp_path, sid)
    child = create_session(tmp_path, expanded_bank, combined, ["NEW"], title="Next check", parent_session=sid)
    assert compare_session(tmp_path, child)["change"].dropna().eq(0).all()
    changed = copy.deepcopy(bank)
    changed["single_skill_items"][0]["stem"] = "Another question"
    with pytest.raises(ValueError, match="changing"):
        expand_results(results, bank, changed)


def test_scan_corrections_keep_raw_and_reject_bank_drift(tmp_path, bank, results):
    corrected = results.copy()
    corrected.loc[0, "item_1"] = 0
    path = approve_scan(tmp_path, results, corrected, bank, reviewer="Teacher", source="scan batch")
    pd.testing.assert_frame_equal(pd.read_excel(path, sheet_name="raw_scan"), results)
    assert len(pd.read_excel(path, sheet_name="corrections")) == 1
    metadata = json.loads(path.with_name("provenance.json").read_text())
    assert metadata["bank_version"] == digest(bank)
    bank["meta"]["subject"] = "Different"
    bp = tmp_path / "wrong_bank.json"
    bp.write_text(json.dumps(bank))
    with pytest.raises(ValueError, match="different bank"):
        score_students(path, bp, tmp_path / "out.xlsx")


def test_real_roster_recognition_and_form_propagation(tmp_path, bank):
    from recognition.score_sheet_template import generate_roster_pdf
    from recognition.recognition_pipeline import process_scans
    form = make_form(bank, [normalize_item(it) for it in reversed(items(bank))])
    generate_roster_pdf(["Student One", "Student Two"], 5, tmp_path / "roster.pdf",
                        cell_metadata_path=tmp_path / "cells.json", form_manifest=form)
    result = process_scans(tmp_path / "cells.json", [tmp_path / "roster.pdf"], tmp_path / "raw.xlsx",
                           debug_dir=tmp_path / "debug")
    assert result.scored.tolist() == [1, 1]
    assert result.filter(like="item_").sum().sum() == 0
    assert "form_manifest" in pd.ExcelFile(tmp_path / "raw.xlsx").sheet_names
    assert list((tmp_path / "debug").glob("*.png"))


@pytest.mark.parametrize("page", ["6_Expert_Review.py", "7_Evaluation.py", "8_Follow_up.py"])
def test_new_pages_render(tmp_path, bank, results, page):
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ui"))
    from lib.state import Workspace
    bp, rp = tmp_path / "bank.json", tmp_path / "results.xlsx"
    bp.write_text(json.dumps(bank), encoding="utf-8")
    results.to_excel(rp, index=False)
    app = AppTest.from_file(str(root / "ui/pages" / page), default_timeout=30)
    app.session_state["_initialised"] = True
    app.session_state["workspace"] = Workspace(name="Test", root=tmp_path, created_at="2026-11-10", bank=bp, results_xlsx=rp)
    app.run()
    assert not app.exception
    if page == "7_Evaluation.py":
        next(b for b in app.button if b.label == "Run evaluation").click().run()
        assert not app.exception
        assert list((tmp_path / "evaluations").glob("*/evaluation.xlsx"))
    if page == "6_Expert_Review.py":
        next(t for t in app.text_input if t.label == "Reviewer name").set_value("Expert")
        next(t for t in app.text_area if t.label == "Reason and evidence for the decision").set_value("Checked graph")
        next(b for b in app.button if b.label == "Save review decision").click().run()
        assert not app.exception
        assert history(tmp_path)[0]["reviewer"] == "Expert"
    if page == "8_Follow_up.py":
        next(m for m in app.multiselect if m.label == "Questions to administer").set_value(["Q1"])
        app.run()
        next(b for b in app.button if b.label == "Create follow-up session").click().run()
        assert not app.exception
        next(b for b in app.button if b.label == "Prepare follow-up quiz and answer key").click().run()
        assert not app.exception
        assert list((tmp_path / "followups").glob("*/quiz.form.json"))


def test_scan_page_requires_review_before_use(tmp_path, bank, results):
    from streamlit.testing.v1 import AppTest
    from recognition.score_sheet_template import generate_roster_pdf
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ui"))
    from lib.state import Workspace
    bp = tmp_path / "bank.json"
    bp.write_text(json.dumps(bank), encoding="utf-8")
    form = make_form(bank, [normalize_item(it) for it in reversed(items(bank))])
    quiz_id, _ = generate_roster_pdf(["Student One", "Student Two"], 5, tmp_path / "roster.pdf",
                                    cell_metadata_path=tmp_path / "cells.json", form_manifest=form)
    app = AppTest.from_file(str(root / "ui/pages/3_Score_and_Mastery.py"), default_timeout=30)
    app.session_state["_initialised"] = True
    app.session_state["workspace"] = Workspace(name="Scan Test", root=tmp_path, created_at="2026-11-10",
                                              bank=bp, roster_pdf=tmp_path / "roster.pdf", roster_cells=tmp_path / "cells.json")
    app.run()
    next(c for c in app.checkbox if c.label.startswith("Use the workspace's printed roster")).check()
    next(t for t in app.text_input if t.label.startswith("Enter the quiz ID")).set_value(quiz_id)
    app.run()
    next(b for b in app.button if b.label == "Recognize roster").click().run()
    assert not app.exception
    assert app.session_state["workspace"].results_xlsx is None
    assert app.session_state["workspace"].scan_draft.exists()
    next(t for t in app.text_input if t.label == "Scan reviewer").set_value("Teacher")
    next(b for b in app.button if b.label == "Approve corrected scan for scoring").click().run()
    assert not app.exception
    output = app.session_state["workspace"].results_xlsx
    assert output.exists()
    assert app.session_state["workspace"].scan_draft is None
    assert "corrections" in pd.ExcelFile(output).sheet_names


def test_materials_navigation_generates_bound_shuffled_roster(tmp_path, bank, monkeypatch):
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ui"))
    from lib.state import Workspace
    import lib.runners
    monkeypatch.setattr(lib.runners, "ollama_reachable", lambda *a, **kw: (False, "offline test"))
    bp = tmp_path / "bank.json"
    bp.write_text(json.dumps(bank), encoding="utf-8")
    app = AppTest.from_file(str(root / "ui/app.py"), default_timeout=30)
    app.session_state["_initialised"] = True
    app.session_state["ollama_host"] = "http://localhost:11434"
    app.session_state["workspace"] = Workspace(name="Materials", root=tmp_path, created_at="2026-11-11", bank=bp)
    app.run()
    app.switch_page("pages/2_Materials.py").run()
    assert not app.exception
    next(c for c in app.checkbox if c.label == "Shuffle item order").check()
    next(c for c in app.checkbox if c.label == "Shuffle A/B/C/D order").check()
    next(b for b in app.button if b.label == "Generate quiz and key").click().run()
    assert not app.exception
    manifest = app.session_state["workspace"].quiz_manifest
    assert manifest and manifest.exists()
    next(b for b in app.button if b.label == "Generate roster sheet").click().run()
    assert not app.exception
    cells = json.loads(app.session_state["workspace"].roster_cells.read_text())
    assert cells["form"]["form_id"] == json.loads(manifest.read_text())["form_id"]
