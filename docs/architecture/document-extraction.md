# Document extraction and model providers

The backend owns provider selection, credentials, source metadata, evidence verification, and workflow
policy. The UI receives only safe processing configuration and scoped document/evidence APIs.

## Provider decision

| Boundary | Selected implementation | Reason and tradeoff |
| --- | --- | --- |
| PDF text and rendering | PDFium through `pypdfium2` | Reads compressed searchable PDFs and renders the same page geometry used by the viewer. Native parsing runs in a bounded child process. |
| Scanned pages | Local Tesseract, English, TSV output | No account or document transfer; TSV supplies word boxes and confidence. Printed upright English is the initial scope; handwriting and arbitrary layouts remain unqualified. |
| Structured model integration | `pydantic-ai-slim[openai,google]` | One typed output contract with maintained Ollama, OpenAI, and Gemini adapters. Only the required provider extras are installed. |
| Local inference | Ollama | No hosted credentials; downloadable model weights and sufficient RAM are required. Model choice must be evaluated independently. |
| Hosted BYOK | OpenAI and Gemini direct APIs | Explicit server keys and model names; structured proposals still require software checks and human review. Vendor retention, agreements, region, and permitted data must be assessed before any real records. |

Primary references: [PDFium Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html),
[Tesseract TSV output](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html),
[Pydantic AI providers](https://pydantic.dev/docs/ai/models/overview/),
[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs),
[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), and
[Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output).
Mistral's [document OCR API](https://docs.mistral.ai/studio/document-processing/basic_ocr) is a hosted
alternative for broader document processing. It is researched but not implemented or called here;
the local baseline avoids another paid service and keeps an explicit OCR adapter boundary.

## Configuration

`MEDBOT_DOCUMENT_EXTRACTOR` selects `fixture` or `pdfium-tesseract`. Compose defaults to the latter;
unit tests default to fixtures. `MEDBOT_AI_PROVIDER` selects `fixture`, `ollama`, `openai`, or `gemini`.
`MEDBOT_AI_MODEL` names the model. Hosted providers require the matching explicit
`MEDBOT_OPENAI_API_KEY` or `MEDBOT_GEMINI_API_KEY`; generic SDK environment keys and default cloud
credentials are not used. OpenAI's endpoint is fixed to its direct API and requests disable storage.
Keys are excluded from settings representations, browser responses, document worker environments,
and plaintext application persistence. Personal keys entered in the UI are encrypted in PostgreSQL and
resolved per initiating user; see [credential security](../security/provider-credentials.md).
No AWS provider is selected by this implementation.

Ollama uses `MEDBOT_OLLAMA_BASE_URL`, default `http://ollama:11434/v1`. Local HTTP endpoints are limited
to loopback, the Compose service, and `host.docker.internal`; remote inference requires HTTPS.
Remote Ollama endpoints are marked as hosted text transfer in the API/UI and require the hosted
evaluation opt-in as well.
Ollama cloud model suffixes and the public Ollama endpoint are rejected. The optional Compose `ai`
profile disables cloud features and exposes no host port. Native host Ollama can use local GPU
acceleration. `make dev-real` downloads and selects `qwen3:4b`; this convenience default does not imply
benchmark parity with a larger model. The measured run used the existing native `qwen3.8:27b` model.

## Source and evidence contract

Stored source bytes are checked against upload size and SHA-256 before extraction or page rendering.
Native PDF lines carry page and normalized top-left bounding boxes. Pages containing images, rotated
pages, and pages without usable text are rendered and OCRed as a whole, so a small text header cannot
hide an otherwise scanned page. OCR lines carry the union of word boxes and minimum word confidence.
The source-page image endpoint enforces authentication, role access, and organization scope before
rendering; it returns private, uncached PNG bytes. Original PDFs remain available for comparison.

The model receives numbered lines and no tools. It returns an allowlisted field path, value, full-line
quote, source index, and confidence. Backend code supplies document IDs, page, block ID, and hash from
the cited source; a model cannot choose them. Schema validation, literal quote/hash/source checks,
field validation, and independent labeled-source conflict detection follow extraction.

Known labels must support the correct field. Narrative lines are allowed only for real-model proposals
with a whole-line quote and a literal, boundary-matched value; their meaning requires human judgment.
Neither literal matching nor OCR confidence establishes clinical truth or visual transcription accuracy.
OCR and real-model proposals remain `REVIEW_REQUIRED` (or `LOW_CONFIDENCE`) until explicitly approved or
corrected. They are excluded from trusted completeness/FHIR values and cannot route. Values awaiting
review are reported separately from absent fields; only genuinely missing items can initiate an
information request. Conflicts require an explained correction. Retrying identical extraction retains
an approval only when both value and exact evidence source match; new conflicts still block routing.

## Failure and resource limits

Uploads are limited to 10 MiB by default. PDF parsing permits 20 pages, at most eight million rendered
pixels per page, a maximum dimension of 8,000 pixels, 2,000 lines, and 80,000 text characters. Lines are
bounded at 2,000 characters. The model boundary further limits input to 500 blocks and 30,000 characters.
Oversized input fails explicitly rather than being silently truncated.

The document child process has a 60-second parent deadline and, on Linux, CPU, address-space, and output
file limits. It inherits a small environment without credentials. Timeout kills its process group and
the parent removes its temporary directory. These limits reduce parser failure impact; they do not
replace production malware scanning and a stronger parser sandbox. OCR is fixed to English and a single
page-layout mode. Handwriting, difficult rotations, complex tables, and real referral diversity require
additional evaluation before expanding scope.

Encrypted, malformed, and unreadable documents return explicit safe 422 errors and failed document
state. An unreadable scan asks for a clearer document. Provider failures return safe 503 errors; model
calls have bounded output, no validation retries, one request budget, and a 100-second overall deadline.
The UI processing proxy allows 180 seconds for OCR plus inference. No automatic provider fallback sends
data elsewhere. A failed immutable input remains blocking; replacing/excluding that input is not yet a
workflow action, so use a new referral with corrected source bytes.

Prompts, OCR text, model output, native stderr, and keys are excluded from normal logs and model
instrumentation. Audit entries record safe provider/action context. The local stack and committed
benchmarks use synthetic data only. Live hosted models have contract coverage but have not been called
with keys in this implementation task.
