"""Isolated generation worker. Immutable inputs, staged outputs, no UI state writes."""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
import traceback

from assessment import jobs
from assessment.provenance import REPO_ROOT, source_snapshot
from assessment.storage import atomic_bytes, atomic_json


def model_identity(client):
    info = {"requested_model": getattr(client, "model", "unknown"), "digest": None}
    try:
        response = client.client.list()
        body = response.model_dump() if hasattr(response, "model_dump") else response
        for entry in body.get("models", []):
            if (entry.get("model") or entry.get("name")) in {info["requested_model"], info["requested_model"] + ":latest"}:
                info.update(digest=entry.get("digest"), details=entry)
                break
    except Exception as exc:
        info["resolution_note"] = f"Model digest unavailable: {type(exc).__name__}"
    return info


def execute(root, job_id, token, *, client_factory=None):
    current = jobs.get(root, job_id)
    if current["status"] != "running" or current["token"] != token or current["cancel_requested"]:
        raise ValueError("Worker does not hold a valid job lease.")
    spec = jobs.request(root, job_id)
    code, _ = source_snapshot()
    if code["source_sha256"] != spec["code"]["source_sha256"]:
        raise ValueError("Application source changed after queuing. Retry to record and use the current version.")
    directory = jobs.folder(root, job_id)
    payload = spec["payload"]
    if spec["kind"] == "ocr":
        from assessment.curriculum_ocr import transcribe
        source = directory / "inputs" / spec["inputs"]["pdf"]["file"]
        extraction = transcribe(source.read_bytes(), payload["pages"], payload.get("language", "eng"))
        target = directory / "outputs/extraction.json"
        atomic_json(target, extraction)
        current = jobs.get(root, job_id)
        if current["status"] != "running" or current["token"] != token or current["cancel_requested"]:
            raise ValueError("OCR job cancelled or lease expired.")
        if source_snapshot()[0]["source_sha256"] != code["source_sha256"]:
            raise ValueError("Application source changed during OCR; retry.")
        result = {"extraction": "outputs/extraction.json", "artifacts": {"outputs/extraction.json": hashlib.sha256(target.read_bytes()).hexdigest()}}
        atomic_json(directory / "result.json", result)
        return result
    if client_factory is None:
        from agent.llm_client import OllamaClient
        client_factory = OllamaClient
    client = client_factory(model=payload.get("model", "gemma4:e4b"), host=payload.get("host", "http://localhost:11434"),
                            thinking=payload.get("thinking") is not False,
                            record_path=directory / "model_calls.jsonl")
    identity = model_identity(client)
    runtime = {"model": identity, "effective_options": getattr(client, "options", {}),
               "thinking": getattr(client, "thinking", None), "resume": "fresh"}
    atomic_json(directory / "runtime.json", runtime)
    paths = {name: directory / "inputs" / entry["file"] for name, entry in spec["inputs"].items()}
    output = directory / "outputs"
    output.mkdir(exist_ok=True)
    # Reuse checkpoints only under identical source, settings, inputs, and resolved model.
    previous_id = current["retry_of"]
    if previous_id and identity["digest"]:
        previous = jobs.request(root, previous_id)
        runtime_path = jobs.folder(root, previous_id) / "runtime.json"
        previous_runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
        matching_inputs = {k: v["sha256"] for k, v in previous["inputs"].items()} == {k: v["sha256"] for k, v in spec["inputs"].items()}
        if (previous["code"]["source_sha256"] == code["source_sha256"] and previous["payload"] == payload
                and matching_inputs and previous_runtime.get("model", {}).get("digest") == identity["digest"]):
            checkpoint = "bank.json" if spec["kind"] == "bank" else "remediation_pack.json" if spec["kind"] == "remediation" else None
            if checkpoint:
                previous_path = jobs.folder(root, previous_id) / "outputs" / checkpoint
                if previous_path.exists():
                    json.loads(previous_path.read_text(encoding="utf-8"))  # reject incomplete JSON
                    atomic_bytes(output / checkpoint, previous_path.read_bytes())
                    runtime["resume"] = f"checkpoint from {previous_id}"
                    atomic_json(directory / "runtime.json", runtime)
    sys.path.insert(0, str(REPO_ROOT / "agent"))
    if spec["kind"] == "curriculum":
        from assessment.curriculum_maps import propose, source_sections
        from assessment.contracts import digest
        if digest(source_sections(root, payload["source_id"])) != payload["source_sections_sha256"]:
            raise ValueError("Curriculum extraction changed since this job was queued.")
        version = propose(root, payload["source_id"], payload["selected_sections"], payload["scope"], client=client)
        result = {"map_version": version}
    elif spec["kind"] == "bank":
        from agent.bank_generator import generate_bank, BankState
        topic = json.loads(paths["spec"].read_text(encoding="utf-8"))
        bank, _ = generate_bank(topic, output / "bank.json", client=client, review_root=root,
                                target_per_skill=payload["target_per_skill"], target_multi=payload["target_multi"],
                                max_iterations=payload["max_iterations"], thinking=payload.get("thinking"), resume=True)
        coverage = BankState(topic, payload["target_per_skill"], payload["target_multi"])
        coverage.single_items, coverage.multi_items = bank["single_skill_items"], bank["multi_skill_items"]
        if not coverage.coverage()["complete"]:
            raise ValueError("Generation stopped before coverage was complete. The verified partial bank is saved for retry.")
        result = {"bank": "outputs/bank.json"}
    elif spec["kind"] == "narratives":
        from cluster_narrative import generate_cluster_narratives, generate_student_narratives
        generate_cluster_narratives(paths["summary"], paths["bank"], output / "narratives.xlsx", client=client)
        result = {"narratives": "outputs/narratives.xlsx"}
        if payload.get("also_students"):
            generate_student_narratives(paths["mastery"], paths["bank"], output / "student_narratives.xlsx",
                                        client=client, only_clusters=payload.get("only_clusters"))
            result["student_narratives"] = "outputs/student_narratives.xlsx"
    elif spec["kind"] == "remediation":
        from remediation_content import generate_remediation_pack
        generate_remediation_pack(paths["paths"], paths["summary"], paths["mastery"], paths["bank"],
                                  output / "remediation_pack.json", client=client, resume=True)
        result = {"pack": "outputs/remediation_pack.json"}
    else:
        raise ValueError("Unsupported generation job.")
    if source_snapshot()[0]["source_sha256"] != code["source_sha256"]:
        raise ValueError("Application source changed during generation; retry with the current version.")
    ending_identity = model_identity(client)
    runtime["model_at_completion"] = ending_identity
    atomic_json(directory / "runtime.json", runtime)
    if identity["digest"] and ending_identity["digest"] and identity["digest"] != ending_identity["digest"]:
        raise ValueError("Model tag changed during generation; outputs were not published.")
    current = jobs.get(root, job_id)
    if current["status"] != "running" or current["token"] != token or current["cancel_requested"]:
        raise ValueError("Job was cancelled or its lease expired; outputs were not published.")
    artifacts = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in output.rglob("*") if p.is_file()}
    result["artifacts"] = artifacts
    atomic_json(directory / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    done = threading.Event()
    def watchdog():
        while not done.wait(2):
            try:
                job = jobs.get(args.root, args.job)
                if (job["token"] != args.token or job["status"] != "running" or job["cancel_requested"]
                        or time.time() - job["heartbeat"] > jobs.LEASE_SECONDS):
                    os._exit(75)  # isolated worker only; supervisor publishes no partial result
            except Exception:
                # A transient database lock should not immediately kill work.
                continue
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        execute(args.root, args.job, args.token)
    except Exception as exc:
        atomic_json(jobs.folder(args.root, args.job) / "error.json", {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        raise
    finally:
        done.set()


if __name__ == "__main__":
    main()
