# Real-document fixtures

Run `python benchmarks/fetch_public_curriculum.py` from the project environment to
download the official England primary mathematics PDF. The downloader records URL,
authority, retrieval timestamp, file hash and text-extraction counts in
`source_catalog.json`. The PDF stays in ignored `sources/`; it is not committed.

Initial local result: **47/47 pages returned text**. The optional regression test
checks the downloaded bytes against the catalog and repeats extraction. CI skips
that check when the downloaded fixture is absent; it never fetches documents during
the offline suite.

No reference skill map, OCR accuracy score or model-quality benchmark is asserted
for this fixture. The expert must select a small unit and independently prepare a
reference. Physical page 6 begins the Year 1 programme in the recorded edition;
check page images and select the desired unit rather than relying on PDF contents
page numbering.

Source: Department for Education, [national curriculum mathematics programmes of
study](https://www.gov.uk/government/publications/national-curriculum-in-england-mathematics-programmes-of-study).
Retain its attribution and licensing information when redistributing source material.
See [validation workflows](../docs/validation_studies.md).
