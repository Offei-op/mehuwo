"""Run records: source archive, installed versions, and exact model exchanges."""
import hashlib
import importlib.metadata
import io
import json
import platform
import threading
import uuid
import zipfile
from pathlib import Path

from assessment.contracts import now

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PROTOCOL = "2026-11-16.1"


def source_snapshot():
    paths = []
    for directory in ("assessment", "agent", "curriculum", "mastery", "recognition", "ui", "tests", "benchmarks"):
        paths.extend((REPO_ROOT / directory).rglob("*.py"))
    paths += [REPO_ROOT / name for name in ("requirements.txt", "requirements-tested.lock", "VERSION") if (REPO_ROOT / name).exists()]
    records, output = {}, io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            name = path.relative_to(REPO_ROOT).as_posix()
            content = path.read_bytes()
            records[name] = hashlib.sha256(content).hexdigest()
            archive.writestr(name, content)
    fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    return {"source_sha256": fingerprint, "files": records,
            "app_version": (REPO_ROOT / "VERSION").read_text().strip() if (REPO_ROOT / "VERSION").exists() else "unversioned",
            "prompt_protocol": PROMPT_PROTOCOL}, output.getvalue()


def environment():
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions() if d.metadata["Name"]))}


class Recorder:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def append(self, event, **data):
        import os
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": now(), "event": event, **data}, default=str, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def chat(self, client, kwargs):
        call_id = str(uuid.uuid4())
        request_hash = hashlib.sha256(json.dumps(kwargs, sort_keys=True, default=str).encode()).hexdigest()
        self.append("request", call_id=call_id, request_sha256=request_hash, request=kwargs)
        try:
            response = client.chat(**kwargs)
            body = response.model_dump() if hasattr(response, "model_dump") else response
            self.append("response", call_id=call_id, response=body)
            return response
        except Exception as exc:
            self.append("error", call_id=call_id, error=f"{type(exc).__name__}: {exc}")
            raise
