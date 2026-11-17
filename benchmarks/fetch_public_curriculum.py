"""Fetch one official real-document fixture; does not create a gold skill map."""
import hashlib
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assessment.curriculum_maps import extract_sections
from assessment.contracts import now


def main():
    url = "https://assets.publishing.service.gov.uk/media/5a7da548ed915d2ac884cb07/PRIMARY_national_curriculum_-_Mathematics_220714.pdf"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    sections = extract_sections(response.content, "curriculum.pdf")
    folder = Path(__file__).resolve().parent
    (folder / "sources").mkdir(exist_ok=True)
    (folder / "sources/england_primary_mathematics.pdf").write_bytes(response.content)
    record = {"source_url": url,
              "landing_page": "https://www.gov.uk/government/publications/national-curriculum-in-england-mathematics-programmes-of-study",
              "authority": "Department for Education, England", "language": "English", "retrieved_at": now(),
              "sha256": hashlib.sha256(response.content).hexdigest(), "pdf_pages": len(sections),
              "readable_pages": sum(bool(s["text"]) for s in sections),
              "status": "Extraction smoke test only; no expert reference or model-quality result"}
    (folder / "source_catalog.json").write_text(json.dumps([record], indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
