# ADR 0004: Local OCR and typed multi-provider extraction

- Status: Accepted
- Date: 2026-10-20

## Context

Fixture text cannot process real scanned or compressed PDFs. Development must remain possible without
paid accounts or AWS credentials, while deployments need a local model option and their own hosted
OpenAI/Gemini keys. Evidence must remain inspectable and model confidence must not grant workflow trust.

## Decision

Use PDFium for native text and rendering, local Tesseract for scan OCR, and Pydantic AI slim provider
adapters for structured Ollama/OpenAI/Gemini proposals. Keep these in-process application adapters;
only native document parsing/OCR runs in a bounded child process. Add an optional local Ollama Compose
profile rather than making every developer download a model. Configure hosted keys exclusively on the
backend and prohibit default cloud credential fallback.

Backend-owned evidence links include immutable source identity/hash, page, line, quote, extraction
method, and normalized region. Every real-model/OCR field requires human review, independent of source
quote consistency or reported confidence. Existing policy, authorization, FHIR, and audit remain the
only workflow authority. Narrative text can produce literal review anchors without asserting automatic
semantic verification. Provider choice, dependencies, and alternatives are documented in
[document extraction](../architecture/document-extraction.md).

## Consequences

- Local development needs no paid provider account; model download, RAM, and CPU/GPU performance are
  explicit costs. Smaller local models require their own quality evaluation.
- Hosted keys and extracted text never pass through browser key storage. Hosted data handling remains
  a deployment-specific decision, and changing providers never bypasses review.
- Real extraction adds an executable synthetic benchmark with frozen source/value/layout oracles.
  Hosted SDK contract tests do not substitute for live model quality measurements.
- English printed documents are the initial OCR scope. Native process limits reduce operational risk
  but production still requires stronger isolation, malware scanning, and representative evaluation.
