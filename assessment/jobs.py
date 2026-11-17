"""Durable local jobs. Supervisors own leases; isolated workers never activate outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from assessment.contracts import digest, now
from assessment.provenance import REPO_ROOT, environment, source_snapshot
from assessment.storage import atomic_bytes, atomic_json

KINDS = {"curriculum", "bank", "narratives", "remediation", "ocr"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
LEASE_SECONDS = 90


@contextmanager
def database(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(Path(root) / "jobs.sqlite", timeout=15)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, request_hash TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          retry_of TEXT, token TEXT, heartbeat REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
          error TEXT, result TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_request ON jobs(request_hash)
          WHERE status IN ('queued','running');
        CREATE UNIQUE INDEX IF NOT EXISTS one_worker ON jobs((1)) WHERE status='running';
        CREATE TABLE IF NOT EXISTS job_events (
          seq INTEGER PRIMARY KEY, job_id TEXT NOT NULL, at TEXT NOT NULL,
          event TEXT NOT NULL, detail TEXT NOT NULL);
    """)
    try:
        with con:
            yield con
    finally:
        con.close()


def folder(root, job_id):
    # Job identifiers never become arbitrary filesystem paths.
    uuid.UUID(job_id)
    return Path(root).resolve() / "jobs" / job_id


def event(con, job_id, name, detail=""):
    con.execute("INSERT INTO job_events(job_id,at,event,detail) VALUES(?,?,?,?)", (job_id, now(), name, detail))


def get(root, job_id):
    with database(root) as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise ValueError("Unknown job.")
    return dict(row)


def list_jobs(root):
    with database(root) as con:
        return [dict(r) for r in con.execute("SELECT * FROM jobs ORDER BY created_at DESC")]


def events(root, job_id):
    with database(root) as con:
        return [dict(r) for r in con.execute("SELECT * FROM job_events WHERE job_id=? ORDER BY seq", (job_id,))]


def submit(root, kind, payload, *, files=None, retry_of=None):
    if kind not in KINDS:
        raise ValueError("Unsupported job type.")
    code, archive = source_snapshot()
    snapshots, contents = {}, {}
    for name, path in (files or {}).items():
        if not name.replace("_", "").isalnum():
            raise ValueError("Invalid input name.")
        path = Path(path).resolve()
        content = path.read_bytes()
        target = name + path.suffix
        snapshots[name] = {"file": target, "sha256": hashlib.sha256(content).hexdigest(), "original_path": str(path)}
        contents[target] = content
    request = {"kind": kind, "payload": payload, "inputs": snapshots, "code": code}
    request_hash = digest(request)
    with database(root) as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute("SELECT id FROM jobs WHERE request_hash=? AND status IN ('queued','running')", (request_hash,)).fetchone()
        if existing:
            return existing[0]
        job_id = str(uuid.uuid4())
        directory = folder(root, job_id)
        directory.mkdir(parents=True)
        for name, content in contents.items():
            atomic_bytes(directory / "inputs" / name, content)
        atomic_json(directory / "request.json", request)
        atomic_bytes(directory / "source.zip", archive)
        atomic_json(directory / "environment.json", environment())
        timestamp = now()
        con.execute("INSERT INTO jobs(id,kind,request_hash,status,created_at,updated_at,retry_of) VALUES(?,?,?,?,?,?,?)",
                    (job_id, kind, request_hash, "queued", timestamp, timestamp, retry_of))
        event(con, job_id, "queued", f"Application {code['app_version']}; source {code['source_sha256']}")
    return job_id


def recover(root, stale_after=LEASE_SECONDS):
    with database(root) as con:
        stale = con.execute("SELECT id FROM jobs WHERE status='running' AND heartbeat < ?", (time.time()-stale_after,)).fetchall()
        for row in stale:
            con.execute("UPDATE jobs SET status='interrupted', token=NULL, updated_at=?, error=? WHERE id=? AND status='running' AND heartbeat < ?",
                        (now(), "Worker supervisor heartbeat expired; retry to continue safely.", row[0], time.time()-stale_after))
            if con.execute("SELECT changes()").fetchone()[0]:
                event(con, row[0], "interrupted", "Lease expired; no output activated")
    return len(stale)


def claim(root, job_id):
    with database(root) as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
            return None
        token = str(uuid.uuid4())
        cursor = con.execute("UPDATE jobs SET status='running',token=?,heartbeat=?,updated_at=? WHERE id=? AND status='queued'",
                             (token, time.time(), now(), job_id))
        if cursor.rowcount:
            event(con, job_id, "running")
            return token
    return None


def heartbeat(root, job_id, token):
    with database(root) as con:
        con.execute("UPDATE jobs SET heartbeat=?,updated_at=? WHERE id=? AND token=? AND status='running'",
                    (time.time(), now(), job_id, token))


def finish(root, job_id, token, status, *, error=None, result=None):
    if status not in TERMINAL:
        raise ValueError("Invalid final status.")
    with database(root) as con:
        con.execute("BEGIN IMMEDIATE")
        current = con.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        if status == "succeeded" and current and current[0]:
            status, result = "cancelled", None
        changed = con.execute("UPDATE jobs SET status=?,error=?,result=?,updated_at=? WHERE id=? AND token=? AND status='running'",
                              (status, error, json.dumps(result) if result else None, now(), job_id, token)).rowcount
        if changed:
            event(con, job_id, status, error or "Output preserved; activation is a separate action")


def cancel(root, job_id):
    with database(root) as con:
        row = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row[0] in {"queued", "running"}:
            con.execute("UPDATE jobs SET cancel_requested=1,status=?,updated_at=? WHERE id=?",
                        ("cancelled" if row[0] == "queued" else "running", now(), job_id))
            event(con, job_id, "cancel_requested")


def request(root, job_id):
    data = json.loads((folder(root, job_id) / "request.json").read_text(encoding="utf-8"))
    if digest(data) != get(root, job_id)["request_hash"]:
        raise ValueError("Job request was modified after submission.")
    for entry in data["inputs"].values():
        content = (folder(root, job_id) / "inputs" / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("An input snapshot was modified after submission.")
    return data


def retry(root, job_id):
    old = get(root, job_id)
    if old["status"] not in {"failed", "cancelled", "interrupted"}:
        raise ValueError("Only failed, cancelled, or interrupted jobs can be retried.")
    spec = request(root, job_id)
    inputs = {name: folder(root, job_id) / "inputs" / entry["file"] for name, entry in spec["inputs"].items()}
    return submit(root, old["kind"], spec["payload"], files=inputs, retry_of=job_id)


def launch(root, job_id):
    if get(root, job_id)["status"] != "queued":
        return
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with (folder(root, job_id) / "supervisor.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-m", "assessment.jobs", "run", "--root", str(Path(root).resolve()), "--job", job_id],
                         cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=flags, start_new_session=os.name != "nt", close_fds=True)


def run(root, job_id):
    recover(root)
    token = None
    while get(root, job_id)["status"] == "queued" and not token:
        token = claim(root, job_id)
        if not token:
            recover(root)
            time.sleep(1)
    if not token:
        return
    child = None
    try:
        with (folder(root, job_id) / "worker.log").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-u", "-m", "assessment.job_worker", "--root", str(Path(root).resolve()),
                                      "--job", job_id, "--token", token], cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            while child.poll() is None:
                current = get(root, job_id)
                if current["cancel_requested"] or current["token"] != token or current["status"] != "running":
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                    finish(root, job_id, token, "cancelled")
                    return
                heartbeat(root, job_id, token)
                time.sleep(1)
        if get(root, job_id)["cancel_requested"]:
            finish(root, job_id, token, "cancelled")
        elif child.returncode == 0:
            result = json.loads((folder(root, job_id) / "result.json").read_text(encoding="utf-8"))
            finish(root, job_id, token, "succeeded", result=result)
        else:
            error_path = folder(root, job_id) / "error.json"
            detail = json.loads(error_path.read_text()).get("error") if error_path.exists() else f"Worker exited with code {child.returncode}"
            finish(root, job_id, token, "failed", error=detail)
    except Exception as exc:
        finish(root, job_id, token, "failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        if child is not None and child.poll() is None:
            child.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    run(args.root, args.job)
