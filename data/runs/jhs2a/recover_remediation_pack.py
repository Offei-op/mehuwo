"""Recovery tool for a corrupted `remediation_pack.json`.

The `_save()` function in `agent/remediation_content.py` writes the pack
non-atomically (`Path.write_text` is not crash-safe). If the script was
killed mid-write, or if two processes wrote to the same file at once,
you can end up with a file like `{}{ "subject": ...` — two JSON blobs
concatenated, which json.loads rejects with "Extra data: ...".

This script walks the corrupt file character by character with
JSONDecoder.raw_decode, keeps the longest valid prefix that looks like a
pack (a dict containing `clusters`), and writes it to a `.recovered`
file alongside the original.

Usage:
    cd <workspace folder with the corrupt remediation_pack.json>
    python recover_remediation_pack.py
    # if the recovered file looks right:
    move remediation_pack.json remediation_pack.json.corrupt
    move remediation_pack.json.recovered remediation_pack.json
    # then re-run remediation_content.py with --resume; it'll pick up
    # from where the recovery left off.

Read-only against the source file. Won't overwrite anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def recover(src: Path) -> Path | None:
    text = src.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()

    longest_obj = None
    longest_end = 0
    i = 0
    n = len(text)
    attempts = 0

    while i < n:
        # raw_decode skips whitespace, so we only need to advance past
        # characters that clearly aren't the start of a JSON value.
        if text[i] not in "{[\"-0123456789tfn":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
            attempts += 1
            # We're looking for the remediation pack shape specifically.
            # Plain objects (e.g. a stray {} from a previous bad write)
            # aren't useful.
            if isinstance(obj, dict) and "clusters" in obj:
                if end > longest_end:
                    longest_obj = obj
                    longest_end = end
            i = end
        except json.JSONDecodeError:
            i += 1

    if longest_obj is None:
        print(f"  no recoverable pack found in {src.name} "
              f"(scanned {attempts} JSON candidates)")
        return None

    dst = src.with_suffix(src.suffix + ".recovered")
    dst.write_text(
        json.dumps(longest_obj, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    clusters = longest_obj.get("clusters", {})
    n_steps = sum(len(c.get("path", [])) for c in clusters.values()
                  if isinstance(c, dict))
    print(f"  recovered {len(clusters)} cluster(s), {n_steps} step(s)")
    print(f"  → wrote {dst}")
    return dst


def main() -> int:
    if len(sys.argv) > 1:
        targets = [Path(p) for p in sys.argv[1:]]
    else:
        targets = [Path("remediation_pack.json")]

    found_any = False
    for src in targets:
        if not src.exists():
            print(f"× {src} does not exist")
            continue
        found_any = True
        print(f"\n{src}:")
        recover(src)

    if not found_any:
        print(
            "\nNo file given and `remediation_pack.json` not found in CWD.\n"
            "Usage:  python recover_remediation_pack.py "
            "[path/to/remediation_pack.json ...]"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
