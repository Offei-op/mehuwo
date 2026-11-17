import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

from assessment import jobs
from assessment.storage import atomic_json
from assessment.job_worker import execute
from assessment.backups import create_backup, restore_backup
from assessment.provenance import Recorder

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
from lib.state import Workspace
from lib.jobs import adopt


def test_queue_deduplicates_snapshots_and_links_retry(tmp_path):
    input_path = tmp_path / "topic.json"
    input_path.write_text('{"v":1}')
    job_id = jobs.submit(tmp_path, "bank", {}, files={"spec": input_path})
    assert jobs.submit(tmp_path, "bank", {}, files={"spec": input_path}) == job_id
    input_path.write_text('{"v":2}')
    assert jobs.request(tmp_path, job_id)["inputs"]["spec"]["sha256"] == hashlib.sha256(b'{"v":1}').hexdigest()
    jobs.cancel(tmp_path, job_id)
    retry = jobs.retry(tmp_path, job_id)
    assert retry != job_id and jobs.get(tmp_path, retry)["retry_of"] == job_id
    assert jobs.get(tmp_path, job_id)["status"] == "cancelled"
    assert jobs.request(tmp_path, retry)["inputs"]["spec"]["sha256"] == jobs.request(tmp_path, job_id)["inputs"]["spec"]["sha256"]


def test_leases_exclude_competing_workers_and_fence_old_results(tmp_path):
    first = jobs.submit(tmp_path, "bank", {"attempt": 1})
    second = jobs.submit(tmp_path, "bank", {"attempt": 2})
    token = jobs.claim(tmp_path, first)
    assert token and jobs.claim(tmp_path, second) is None
    with jobs.database(tmp_path) as con:
        con.execute("UPDATE jobs SET heartbeat=0 WHERE id=?", (first,))
    assert jobs.recover(tmp_path) == 1
    jobs.finish(tmp_path, first, token, "succeeded", result={"bad": True})
    assert jobs.get(tmp_path, first)["status"] == "interrupted"
    assert jobs.claim(tmp_path, second)


def test_background_subprocess_records_failure_without_model_call(tmp_path):
    path = tmp_path / "input.json"
    path.write_text("{}")
    job_id = jobs.submit(tmp_path, "bank", {}, files={"spec": path})
    snapshot = jobs.folder(tmp_path, job_id) / "inputs/spec.json"
    snapshot.write_text("modified")
    jobs.launch(tmp_path, job_id)
    deadline = time.monotonic() + 20
    while jobs.get(tmp_path, job_id)["status"] not in jobs.TERMINAL and time.monotonic() < deadline:
        time.sleep(.1)
    record = jobs.get(tmp_path, job_id)
    assert record["status"] == "failed"
    assert "snapshot was modified" in record["error"]
    assert not (jobs.folder(tmp_path, job_id) / "model_calls.jsonl").exists()


def test_worker_rejects_changed_source_before_model(tmp_path, monkeypatch):
    from assessment import job_worker
    job_id = jobs.submit(tmp_path, "bank", {})
    token = jobs.claim(tmp_path, job_id)
    monkeypatch.setattr(job_worker, "source_snapshot", lambda: ({"source_sha256": "changed"}, b""))
    with pytest.raises(ValueError, match="source changed"):
        execute(tmp_path, job_id, token, client_factory=lambda **kw: pytest.fail("Must not contact model"))


def test_model_exchange_recording_includes_options_response_and_failure(tmp_path):
    from agent.llm_client import OllamaClient
    class Transport:
        def chat(self, **kwargs):
            if kwargs["messages"][-1]["content"] == "fail":
                raise ValueError("service unavailable")
            return {"message": {"content": "answer"}}
    client = OllamaClient(record_path=tmp_path / "calls.jsonl")
    client.client = Transport()
    assert client.generate("question", options={"temperature": .2}) == "answer"
    with pytest.raises(ValueError):
        client.generate("fail")
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [r["event"] for r in rows] == ["request", "response", "request", "error"]
    assert rows[0]["request"]["options"]["temperature"] == .2
    assert rows[0]["call_id"] == rows[1]["call_id"]


def test_workspace_conflict_and_backup_restore(tmp_path):
    root = tmp_path / "workspace"
    bank = root / "bank.json"
    atomic_json(bank, {"sample": True})
    workspace = Workspace("Example", root, "2026-11-16", bank=bank)
    workspace.to_disk()
    stale = Workspace.from_disk(root)
    workspace.name = "Updated"
    workspace.to_disk()
    with pytest.raises(RuntimeError, match="another session"):
        stale.to_disk()
    job_id = jobs.submit(root, "bank", {})
    with pytest.raises(ValueError, match="Finish or cancel"):
        create_backup(root)
    jobs.cancel(root, job_id)
    archive = create_backup(root)
    restored_root = restore_backup(archive, tmp_path / "restored")
    restored = Workspace.from_disk(restored_root)
    assert restored.name == "Updated" and restored.bank == restored_root / "bank.json"
    assert restored.bank.read_bytes() == bank.read_bytes()
    assert jobs.get(restored_root, job_id)["status"] == "cancelled"
    restored.to_disk()
    with pytest.raises(ValueError, match="new destination"):
        restore_backup(archive, restored_root)


def test_restore_rejects_path_traversal_before_writing(tmp_path):
    archive = tmp_path / "bad.zip"
    payload = b"bad"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("../escape", payload)
        out.writestr("backup_manifest.json", json.dumps({"format": 1, "files": {"../escape": hashlib.sha256(payload).hexdigest()}}))
    with pytest.raises(ValueError, match="Unsafe"):
        restore_backup(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_adoption_rejects_stale_inputs_and_changed_outputs(tmp_path):
    summary = tmp_path / "summary.xlsx"
    summary.write_bytes(b"original")
    workspace = Workspace("Example", tmp_path, "today", cluster_summary_xlsx=summary)
    workspace.to_disk()
    job_id = jobs.submit(tmp_path, "narratives", {}, files={"summary": summary})
    output = jobs.folder(tmp_path, job_id) / "outputs/narratives.xlsx"
    output.parent.mkdir()
    output.write_bytes(b"generated")
    result = {"narratives": "outputs/narratives.xlsx", "artifacts": {"outputs/narratives.xlsx": hashlib.sha256(b"generated").hexdigest()}}
    token = jobs.claim(tmp_path, job_id)
    jobs.finish(tmp_path, job_id, token, "succeeded", result=result)
    summary.write_bytes(b"new input")
    with pytest.raises(ValueError, match="active inputs changed"):
        adopt(workspace, job_id)
    summary.write_bytes(b"original")
    output.write_bytes(b"changed")
    with pytest.raises(ValueError, match="saved output has changed"):
        adopt(workspace, job_id)
    output.write_bytes(b"generated")
    updated = adopt(workspace, job_id)
    assert updated.cluster_summary_narratives_xlsx == output
    assert workspace.cluster_summary_narratives_xlsx is None
    assert jobs.events(tmp_path, job_id)[-1]["event"] == "activated"


def test_cancel_wins_race_with_success(tmp_path):
    job_id = jobs.submit(tmp_path, "bank", {})
    token = jobs.claim(tmp_path, job_id)
    jobs.cancel(tmp_path, job_id)
    jobs.finish(tmp_path, job_id, token, "succeeded", result={"bank": "partial"})
    assert jobs.get(tmp_path, job_id)["status"] == "cancelled"
    assert jobs.get(tmp_path, job_id)["result"] is None


def test_bank_retry_restores_verified_checkpoint_and_keeps_old_attempt(tmp_path):
    from assessment.curriculum_maps import register_spec, decide
    from agent.llm_client import AgentResult
    topic = {"subject": "Arithmetic", "node_order": ["Counting"], "prerequisites": {"Counting": []}}
    version = register_spec(tmp_path, topic)
    decide(tmp_path, version, reviewer="Teacher", rationale="Checked", decision="approved", confirmed=True)
    path = tmp_path / "topic.json"
    atomic_json(path, topic)
    class Client:
        model = "test"
        crash = True
        def __init__(self, **kwargs):
            self.client = self
        def list(self):
            return {"models": [{"model": "test", "digest": "fixed-test-model"}]}
        def agent_loop(self, prompt, tools, **kwargs):
            coverage = next(t for t in tools if t.name == "coverage_report").handler({})
            count = coverage["total_single_items"]
            assert count == (0 if self.crash else 1)
            item = {"stem": f"Count this collection of {count + 2} objects.",
                    "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
                    "correct_option": "B" if count == 0 else "C", "solution_expression": f"Fraction({count+2}, 1)",
                    "distractor_rationales": {k: "Incorrect count" for k in "ABCD" if k != ("B" if count == 0 else "C")},
                    "q_vector": [1], "target_node": "Counting", "difficulty_hint": "easy"}
            result = next(t for t in tools if t.name == "add_item").handler(item)
            assert result["ok"], result
            kwargs["on_tool_call"]("add_item", item, result)
            if self.crash:
                raise RuntimeError("Simulated model disconnection")
            return AgentResult("DONE", 1)
    payload = {"target_per_skill": 2, "target_multi": 0, "max_iterations": 10}
    first = jobs.submit(tmp_path, "bank", payload, files={"spec": path})
    token = jobs.claim(tmp_path, first)
    with pytest.raises(RuntimeError, match="disconnection"):
        execute(tmp_path, first, token, client_factory=Client)
    jobs.finish(tmp_path, first, token, "failed", error="Simulated model disconnection")
    old_output = jobs.folder(tmp_path, first) / "outputs/bank.json"
    old_bytes = old_output.read_bytes()
    second = jobs.retry(tmp_path, first)
    Client.crash = False
    result = execute(tmp_path, second, jobs.claim(tmp_path, second), client_factory=Client)
    bank = json.loads((jobs.folder(tmp_path, second) / result["bank"]).read_text())
    assert len(bank["single_skill_items"]) == 2
    assert len({i["item_id"] for i in bank["single_skill_items"]}) == 2
    assert old_output.read_bytes() == old_bytes
    assert "checkpoint from" in json.loads((jobs.folder(tmp_path, second) / "runtime.json").read_text())["resume"]
