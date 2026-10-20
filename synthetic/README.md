# Synthetic referral fixtures

Everything in this directory is generated test data. It is not derived from a real person,
referral, provider, or medical record.

Run `make generate-fixtures` to deterministically rebuild the three Phase 1 PDFs and their
ground-truth JSON files. The generator intentionally disables PDF stream compression because the
Phase 1 fixture extractor reads literal PDF text operators. Real-world PDF parsing, scanned-image
handling, and OCR are later provider responsibilities.

The ground-truth files are test oracles, not model output. Each records the expected extracted
fields and evidence quotes, missing fields/documents, routing queue, review trigger, post-processing
state, and source PDF SHA-256. The prompt-injection fixture explicitly records that its hostile text
must remain inert document data.

`make eval` measures only fixture and ground-truth integrity. It deliberately reports model-quality
and workflow-quality metrics as unavailable until an evaluation runner executes the full pipeline.
