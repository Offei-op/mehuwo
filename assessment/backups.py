"""Checksummed workspace archives and restore into a new directory.

SQLite databases use online backups; each database is consistent independently.
Create backups between editing sessions, with generation stopped.
"""
import argparse
import hashlib
import io
import json
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from assessment import jobs
from assessment.storage import atomic_bytes, file_lock
from assessment.contracts import now


def create_backup(root):
    root = Path(root).resolve()
    output = root / "backups" / f"workspace-{uuid.uuid4()}.zip"
    data = io.BytesIO()
    inventory = {}
    with file_lock(root / "workspace.lock"), jobs.database(root) as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
            raise ValueError("Finish or cancel queued and running jobs before creating a backup.")
        manifest = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
            def add(name, content):
                inventory[name] = hashlib.sha256(content).hexdigest()
                archive.writestr(name, content)
            for path in root.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(root)
                if relative.parts[0] == "backups" or path.name.startswith(".") or path.suffix == ".lock" or path.name.endswith(("-wal", "-shm", "-journal")) or path.name in {"workspace.json", "backup_manifest.json"}:
                    continue
                if path.suffix == ".sqlite":
                    with tempfile.TemporaryDirectory() as tmp:
                        copied = Path(tmp) / path.name
                        source = sqlite3.connect(path)
                        destination = sqlite3.connect(copied)
                        try:
                            source.backup(destination)
                        finally:
                            source.close()
                            destination.close()
                        add(relative.as_posix(), copied.read_bytes())
                else:
                    add(relative.as_posix(), path.read_bytes())
            # Bundle externally referenced inputs (for example the demo bank).
            for key, value in list(manifest.items()):
                if key in {"root", "name", "created_at", "validation_report"} or not isinstance(value, str):
                    continue
                path = Path(value)
                if path.is_absolute() and path.exists() and not path.resolve().is_relative_to(root):
                    relative = Path("imported") / key / path.name
                    if path.is_dir():
                        for child in path.rglob("*"):
                            if child.is_file():
                                add((relative / child.relative_to(path)).as_posix(), child.read_bytes())
                    else:
                        add(relative.as_posix(), path.read_bytes())
                    manifest[key] = str(root / relative)
            add("workspace.json", json.dumps(manifest, indent=2).encode())
            archive.writestr("backup_manifest.json", json.dumps({"format": 1, "created_at": now(), "files": inventory}))
    atomic_bytes(output, data.getvalue())
    return output


def restore_backup(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Restore requires a new destination directory; existing workspaces are never overwritten.")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members.")
        record = json.loads(archive.read("backup_manifest.json"))
        if record["format"] != 1 or set(names) != set(record["files"]) | {"backup_manifest.json"}:
            raise ValueError("Backup inventory mismatch.")
        contents = {}
        for name, expected in record["files"].items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError("Unsafe archive path.")
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"Backup checksum mismatch: {name}")
            contents[name] = content
        if "workspace.json" not in contents:
            raise ValueError("Backup has no workspace manifest.")
        # Validate databases before touching the destination.
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in contents.items():
                if name.endswith(".sqlite"):
                    path = Path(tmp) / str(uuid.uuid4())
                    path.write_bytes(content)
                    connection = sqlite3.connect(path)
                    try:
                        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError(f"Invalid database: {name}")
                    finally:
                        connection.close()
        destination.mkdir(parents=True, exist_ok=False)
        for name, content in contents.items():
            atomic_bytes(destination / name, content)
        atomic_bytes(destination / "backup_manifest.json", archive.read("backup_manifest.json"))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("destination")
    args = parser.parse_args()
    print(restore_backup(args.archive, args.destination))
