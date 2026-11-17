import copy
import hashlib
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from reportlab.pdfgen import canvas

from assessment import curriculum_ocr as ocr
from assessment.contracts import now
from assessment.curriculum_maps import save_source, source_sections, save_proposal, get_map, review_state
from assessment.validation_studies import create_case, benchmark, create_protocol, run_pilot, records
from assessment.review import record_review
from test_assessment_workflows import bank, results
from test_curriculum_maps import proposal


def pdf():
    stream = io.BytesIO()
    doc = canvas.Canvas(stream)
    doc.drawString(50, 700, "Counting and addition")
    doc.showPage()
    doc.save()
    return stream.getvalue()


def test_page_selection_limits_and_rendering():
    assert ocr.page_numbers("3-5,8,3") == [3, 4, 5, 8]
    for bad in ("0", "5-2", "1-21", "1;cmd", "251"):
        with pytest.raises(ValueError):
            ocr.page_numbers(bad)
    assert ocr.pdf_page(pdf(), 1).width > 0
    with pytest.raises(ValueError):
        ocr.pdf_page(pdf(), 2)


def test_ocr_keeps_physical_page_ids_and_confidence(monkeypatch):
    monkeypatch.setattr(ocr, "engine", lambda: "fake-tesseract")
    class Response:
        stdout = ""
    def run(command, **kwargs):
        response = Response()
        if command[-1] == "--version":
            response.stdout = "tesseract fake-test\n"
        else:
            assert command[-3:] == ["-l", "eng", "tsv"]
            response.stdout = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n5\t1\t1\t1\t1\t1\t90\tCount\n5\t1\t1\t1\t1\t2\t80\tobjects\n"
        return response
    monkeypatch.setattr(ocr.subprocess, "run", run)
    extracted = ocr.transcribe(pdf(), [1])
    assert extracted["sections"][0]["id"] == "page_1"
    assert extracted["sections"][0]["text"] == "Count objects"
    assert extracted["sections"][0]["mean_word_confidence"] == 85


def test_ocr_worker_never_instantiates_an_llm(tmp_path, monkeypatch):
    from assessment import jobs
    from assessment.job_worker import execute
    path = tmp_path / "scan.pdf"
    path.write_bytes(pdf())
    monkeypatch.setattr(ocr, "transcribe", lambda *a, **kw: {"sections": [{"id": "page_1", "text": "Count"}]})
    job_id = jobs.submit(tmp_path, "ocr", {"pages": [1]}, files={"pdf": path})
    result = execute(tmp_path, job_id, jobs.claim(tmp_path, job_id),
                     client_factory=lambda **kw: pytest.fail("OCR must not contact an LLM"))
    assert "extraction" in result
    assert not source_sections_if_any(tmp_path)


def source_sections_if_any(root):
    from assessment.curriculum_maps import sources
    return sources(root)


def test_reviewed_transcription_is_versioned_and_cannot_approve_map(tmp_path):
    data = pdf()
    extraction = {"document_sha256": hashlib.sha256(data).hexdigest(), "engine": "manual",
                  "sections": [{"id": "page_1", "label": "PDF page 1", "text": "C0unt"}]}
    with pytest.raises(ValueError, match="reviewer"):
        ocr.save_reviewed(tmp_path, data, "scan.pdf", extraction, {"page_1": "Count"}, "", False)
    first = ocr.save_reviewed(tmp_path, data, "scan.pdf", extraction, {"page_1": "Count objects."}, "Teacher", True)
    second = ocr.save_reviewed(tmp_path, data, "scan.pdf", extraction, {"page_1": "Count two objects."}, "Teacher", True)
    assert first != second
    assert source_sections(tmp_path, first)[0]["text"] == "Count objects."
    candidate = {"subject": "Math", "learner_level": "Primary", "skills": [{"skill_id": "Count",
        "description": "Count objects", "basis": "explicit", "source_section": "page_1", "quote": "Count objects.", "rationale": "Objective"}], "prerequisites": []}
    version = save_proposal(tmp_path, candidate, source_id=first, selected_sections=["page_1"], scope="Counting")
    assert review_state(tmp_path, version)["decision"] == "draft"
    spec = json.loads(get_map(tmp_path, version)["spec"])
    assert spec["curriculum_source"]["document_sha256"] == hashlib.sha256(data).hexdigest()
    assert spec["curriculum_source"]["extraction_version"] == first


def test_benchmark_alignment_and_evidence_are_explicit(tmp_path, proposal):
    source_id = save_source(tmp_path, b"Count objects up to twenty.\nAdd two collections of objects.", "source.txt")
    reference = {"subject": "Math", "node_order": ["Count", "Add", "Subtract"],
                 "prerequisites": {"Count": [], "Add": ["Count"], "Subtract": ["Count"]}}
    case = create_case(tmp_path, source_id=source_id, selected_sections=["lines_1"], scope="Arithmetic",
                       reference=reference, reviewer="Expert", independent=True, split="evaluation")
    candidate = save_proposal(tmp_path, proposal, source_id=source_id, selected_sections=["lines_1"], scope="Arithmetic")
    alignment = {"Counting": "Count", "Addition": "Add"}
    evidence = {"skill:Counting": "supported", "skill:Addition": "uncertain"}
    version, report = benchmark(tmp_path, case, candidate, alignment, evidence, reviewer="Second expert", minutes=4, notes="Subtraction missing")
    assert report["metrics"]["skill_precision"] == 1
    assert report["metrics"]["skill_recall"] == pytest.approx(2/3)
    assert report["metrics"]["edge_recall"] == .5
    assert report["metrics"]["explicit_claim_support_fraction"] == .5
    assert report["reference_precedes_candidate"]
    assert records(tmp_path, "benchmark_review")[0]["version"] == version
    with pytest.raises(ValueError, match="one-to-one"):
        benchmark(tmp_path, case, candidate, {"Counting": "Count", "Addition": "Count"}, evidence, reviewer="Expert", minutes=2, notes="Duplicate")
    with pytest.raises(ValueError, match="every explicit"):
        benchmark(tmp_path, case, candidate, alignment, {}, reviewer="Expert", minutes=2, notes="No judgments")


def pilot(root, bank):
    for scope in ["skill_map"] + [q["item_id"] for q in bank["single_skill_items"] + bank["multi_skill_items"]]:
        record_review(root, bank, bank, scope=scope, reviewer="Teacher", decision="approved", rationale="Checked")
    return create_protocol(root, bank, title="Pilot", owner="Teacher", cohort="Pseudonymous class",
                           reference_assessment_id="REF", reference_item_ids=["R1", "R2"],
                           reference_procedure="Separate oral tasks scored with prespecified rubric", independent=True)


def test_pilot_freezes_inputs_and_is_idempotent(tmp_path, bank, results):
    protocol = pilot(tmp_path, bank)
    collected = now()
    reference = pd.DataFrame({"student_idx": [0, 0, 1, 1], "skill": ["A", "B", "A", "B"], "mastered": [1, 0, 0, 1],
                               "assessment_id": ["REF"]*4, "assessor": ["Independent teacher"]*4, "collected_at": [collected]*4})
    folder = run_pilot(tmp_path, protocol, bank, results, reference, diagnostic_assessment_id="DIAG", collected_at=collected, assessor_blinded=True)
    report_bytes = (folder / "evaluation.xlsx").read_bytes()
    assert run_pilot(tmp_path, protocol, bank, results, reference, diagnostic_assessment_id="DIAG", collected_at=collected, assessor_blinded=True) == folder
    assert (folder / "evaluation.xlsx").read_bytes() == report_bytes
    assert len(records(tmp_path, "pilot_run")) == 1
    changed = copy.deepcopy(bank)
    changed["meta"]["subject"] = "Changed"
    with pytest.raises(ValueError, match="bank changed"):
        run_pilot(tmp_path, protocol, changed, results, reference, diagnostic_assessment_id="DIAG", collected_at=collected, assessor_blinded=True)
    with pytest.raises(ValueError, match="separate assessment"):
        run_pilot(tmp_path, protocol, bank, results, reference, diagnostic_assessment_id="REF", collected_at=collected, assessor_blinded=True)
    with pytest.raises(ValueError, match="before data collection"):
        run_pilot(tmp_path, protocol, bank, results, reference, diagnostic_assessment_id="DIAG", collected_at="2000-01-01T00:00:00+00:00", assessor_blinded=True)
    reference.loc[0, "skill"] = "Unknown"
    with pytest.raises(ValueError, match="unknown student or skill"):
        run_pilot(tmp_path, protocol, bank, results, reference, diagnostic_assessment_id="DIAG", collected_at=collected, assessor_blinded=True)


def test_pilot_rejects_reused_questions(tmp_path, bank):
    with pytest.raises(ValueError, match="reuse"):
        create_protocol(tmp_path, bank, title="Pilot", owner="Teacher", cohort="Class", reference_assessment_id="REF",
                        reference_item_ids=["Q1"], reference_procedure="Invalid reuse", independent=True)


def test_validation_ui_loads_without_data(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ui"))
    from lib.state import Workspace
    import lib.runners
    monkeypatch.setattr(lib.runners, "ollama_reachable", lambda *args, **kw: (False, "offline"))
    app = AppTest.from_file(str(root / "ui/app.py"), default_timeout=20)
    app.session_state["_initialised"] = True
    app.session_state["workspace"] = Workspace("Pilot", tmp_path, now())
    app.run().switch_page("pages/10_Validation_Studies.py").run()
    assert not app.exception


def test_public_curriculum_fixture_when_downloaded():
    root = Path(__file__).resolve().parents[1]
    path = root / "benchmarks/sources/england_primary_mathematics.pdf"
    if not path.exists():
        pytest.skip("Run benchmarks/fetch_public_curriculum.py for the optional public-document smoke test")
    from assessment.curriculum_maps import extract_sections
    catalog = json.loads((root / "benchmarks/source_catalog.json").read_text())[0]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == catalog["sha256"]
    sections = extract_sections(path.read_bytes(), path.name)
    assert len(sections) == catalog["pdf_pages"]
    assert sum(bool(s["text"]) for s in sections) == catalog["readable_pages"]
