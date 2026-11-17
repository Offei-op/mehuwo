import copy
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from assessment.curriculum_maps import (extract_sections, save_source, source_sections, propose,
    maps, get_map, decide, require_approved, review_state, save_proposal, register_spec, ProposedMap)
from agent.bank_generator import generate_bank
from agent.llm_client import AgentResult


@pytest.fixture
def proposal():
    return {"subject": "Arithmetic", "learner_level": "Early primary",
            "skills": [
                {"skill_id": "Counting", "description": "Count a collection of objects",
                 "basis": "explicit", "source_section": "lines_1", "quote": "Count objects up to twenty.",
                 "rationale": "The curriculum names counting as a learning objective."},
                {"skill_id": "Addition", "description": "Combine two small collections",
                 "basis": "explicit", "source_section": "lines_1", "quote": "Add two collections of objects.",
                 "rationale": "The curriculum specifies adding collections."}],
            "prerequisites": [{"prerequisite": "Counting", "skill": "Addition", "basis": "inferred",
                               "source_section": "", "quote": "", "rationale": "Counting may support combining quantities; expert review is needed."}],
            "limitations": ["The document does not explicitly specify this prerequisite."]}


def document(root):
    return save_source(root, b"Count objects up to twenty.\nAdd two collections of objects.", "curriculum.txt")


def test_formats_and_empty_scanned_documents(tmp_path):
    assert extract_sections(b"A learning objective", "source.txt")[0]["id"] == "lines_1"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Count objects.</w:t></w:r></w:p></w:body></w:document>')
    assert extract_sections(stream.getvalue(), "curriculum.docx")[0]["text"] == "Count objects."
    from reportlab.pdfgen import canvas
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(40, 700, "Counting objects")
    pdf.showPage()
    pdf.save()
    assert "Counting objects" in extract_sections(output.getvalue(), "curriculum.pdf")[0]["text"]
    blank = io.BytesIO()
    pdf = canvas.Canvas(blank)
    pdf.showPage()
    pdf.save()
    with pytest.raises(ValueError, match="No readable text"):
        extract_sections(blank.getvalue(), "scan.pdf")


def test_model_output_is_only_a_draft(tmp_path, proposal):
    source = document(tmp_path)
    class FakeClient:
        model = "offline"
        def generate_json(self, prompt, **kwargs):
            assert "Count objects" in prompt
            assert "untrusted" in kwargs["system"]
            return ProposedMap.model_validate(proposal)
    version = propose(tmp_path, source, ["lines_1"], "Addition", client=FakeClient())
    spec = json.loads(get_map(tmp_path, version)["spec"])
    assert review_state(tmp_path, version)["decision"] == "draft"
    with pytest.raises(ValueError, match="not been approved"):
        require_approved(tmp_path, spec)
    with pytest.raises(ValueError, match="confirm"):
        decide(tmp_path, version, reviewer="Teacher", rationale="Reviewed", decision="approved")
    decide(tmp_path, version, reviewer="Teacher", rationale="Reviewed sources and the inferred edge",
           decision="approved", confirmed=True)
    assert require_approved(tmp_path, spec) == version


@pytest.mark.parametrize("kind", ["fabricated_quote", "missing_quote", "cycle", "unknown_skill", "duplicate_skill", "approval_flag"])
def test_invalid_proposals_never_enter_registry(tmp_path, proposal, kind):
    source = document(tmp_path)
    if kind == "fabricated_quote":
        proposal["skills"][0]["quote"] = "Invented curriculum claim"
    elif kind == "missing_quote":
        proposal["skills"][0]["quote"] = ""
    elif kind == "cycle":
        proposal["prerequisites"].append({**proposal["prerequisites"][0], "prerequisite": "Addition", "skill": "Counting"})
    elif kind == "unknown_skill":
        proposal["prerequisites"][0]["skill"] = "Unknown"
    elif kind == "duplicate_skill":
        proposal["skills"][1]["skill_id"] = "Counting"
    elif kind == "approval_flag":
        proposal["approved"] = True
    with pytest.raises(ValueError):
        save_proposal(tmp_path, proposal, source_id=source, selected_sections=["lines_1"], scope="Arithmetic")
    assert not maps(tmp_path)


def test_edits_and_revocation_cannot_reuse_approval(tmp_path, proposal):
    source = document(tmp_path)
    version = save_proposal(tmp_path, proposal, source_id=source, selected_sections=["lines_1"], scope="Arithmetic")
    spec = json.loads(get_map(tmp_path, version)["spec"])
    decide(tmp_path, version, reviewer="Reviewer", rationale="Checked", decision="approved", confirmed=True)
    changed = copy.deepcopy(proposal)
    changed["skills"][0]["description"] = "Count objects with one-to-one correspondence"
    newer = save_proposal(tmp_path, changed, source_id=source, selected_sections=["lines_1"], scope="Arithmetic", parent_version=version)
    assert newer != version
    with pytest.raises(ValueError):
        require_approved(tmp_path, json.loads(get_map(tmp_path, newer)["spec"]))
    decide(tmp_path, version, reviewer="Reviewer", rationale="Need independent review", decision="needs_revision")
    with pytest.raises(ValueError):
        require_approved(tmp_path, spec)


def test_generator_guard_runs_before_model_and_before_export(tmp_path):
    spec = {"subject": "Arithmetic", "node_order": ["Counting"], "prerequisites": {"Counting": []}}
    version = register_spec(tmp_path, spec)
    class Client:
        def agent_loop(self, *args, **kwargs):
            pytest.fail("A draft must never reach the question generator")
    with pytest.raises(ValueError):
        generate_bank(spec, tmp_path / "bank.json", client=Client(), review_root=tmp_path)
    assert not (tmp_path / "bank.json").exists()
    decide(tmp_path, version, reviewer="Teacher", rationale="Verified", decision="approved", confirmed=True)
    class RevokingClient:
        def agent_loop(self, *args, **kwargs):
            decide(tmp_path, version, reviewer="Teacher", rationale="Found a problem", decision="rejected")
            return AgentResult(final_message="done", iterations=1, tool_calls=[], finished_cleanly=True)
    with pytest.raises(ValueError):
        generate_bank(spec, tmp_path / "bank.json", client=RevokingClient(), review_root=tmp_path)
    assert not (tmp_path / "bank.json").exists()


def test_selected_source_limit_does_not_silently_truncate(tmp_path):
    source = save_source(tmp_path, b"Learning objective. " * 1500, "big.txt")
    class Client:
        def generate_json(self, *args, **kwargs):
            pytest.fail("Oversized source must not call the model")
    with pytest.raises(ValueError, match="smaller unit"):
        propose(tmp_path, source, ["lines_1"], "Arithmetic", client=Client())


def test_approved_map_generates_verified_questions_with_provenance(tmp_path):
    spec = {"subject": "Arithmetic", "node_order": ["Counting"], "prerequisites": {"Counting": []}}
    version = register_spec(tmp_path, spec)
    decide(tmp_path, version, reviewer="Teacher", rationale="Reviewed counting objective", decision="approved", confirmed=True)
    class Client:
        def agent_loop(self, prompt, tools, **kwargs):
            add = next(tool for tool in tools if tool.name == "add_item")
            result = add.handler({"stem": "How many objects are in a pair?",
                                  "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
                                  "correct_option": "B", "solution_expression": "Fraction(2, 1)",
                                  "distractor_rationales": {"A": "undercount", "C": "overcount", "D": "double count"},
                                  "q_vector": [1], "target_node": "Counting", "difficulty_hint": "easy"})
            assert result["ok"]
            return AgentResult(final_message="DONE", iterations=1, tool_calls=[], finished_cleanly=True)
    bank, _ = generate_bank(spec, tmp_path / "bank.json", client=Client(), review_root=tmp_path,
                             target_per_skill=1, target_multi=0, verbose=False)
    assert bank["meta"]["approved_topic_version"] == version
    assert len(bank["single_skill_items"]) == 1
    assert (tmp_path / "bank.json").exists()


def test_ui_proposal_approval_and_activation(tmp_path, proposal, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from agent import llm_client
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ui"))
    from lib.state import Workspace
    import lib.runners
    monkeypatch.setattr(lib.runners, "ollama_reachable", lambda *a, **kw: (False, "offline"))
    class Client:
        model = "offline"
        def __init__(self, **kwargs):
            pass
        def generate_json(self, *args, **kwargs):
            return ProposedMap.model_validate(proposal)
    monkeypatch.setattr(llm_client, "OllamaClient", Client)
    from assessment import jobs
    from assessment.job_worker import execute
    monkeypatch.setattr(jobs, "launch", lambda *args: None)
    document(tmp_path)
    app = AppTest.from_file(str(root / "ui/app.py"), default_timeout=20)
    app.session_state["_initialised"] = True
    app.session_state["ollama_host"] = "http://localhost:11434"
    app.session_state["workspace"] = Workspace(name="Curriculum", root=tmp_path, created_at="2026-11-12")
    app.run().switch_page("pages/1_Topic_and_Items.py").run()
    next(m for m in app.multiselect if m.label == "Pages or paragraphs for this unit").set_value(["lines_1"])
    next(t for t in app.text_input if t.label == "Topic or unit to assess").set_value("Addition")
    app.run()
    next(b for b in app.button if b.label == "Propose skill map with AI").click().run()
    assert not app.exception
    assert app.session_state["workspace"].topic_spec is None
    job_id = jobs.list_jobs(tmp_path)[0]["id"]
    assert jobs.get(tmp_path, job_id)["status"] == "queued"
    token = jobs.claim(tmp_path, job_id)
    result = execute(tmp_path, job_id, token, client_factory=Client)
    jobs.finish(tmp_path, job_id, token, "succeeded", result=result)
    app.switch_page("pages/9_Jobs_and_History.py").run()
    assert not app.exception
    next(b for b in app.button if b.label == "Review proposed map").click().run()
    app.switch_page("pages/1_Topic_and_Items.py").run()
    assert not app.exception
    assert next(b for b in app.button if b.label == "Use approved map for question generation").disabled
    next(t for t in app.text_input if t.label == "Map reviewer name").set_value("Teacher")
    next(t for t in app.text_area if t.label == "Review rationale").set_value("Checked quotes and inferred prerequisite")
    next(s for s in app.selectbox if s.label == "Map decision").set_value("approved")
    next(c for c in app.checkbox if c.label.startswith("I reviewed the saved skills")).check()
    next(b for b in app.button if b.label == "Record map review decision").click().run()
    assert not app.exception
    next(b for b in app.button if b.label == "Use approved map for question generation").click().run()
    assert not app.exception
    spec = json.loads(app.session_state["workspace"].topic_spec.read_text(encoding="utf-8"))
    assert require_approved(tmp_path, spec)
