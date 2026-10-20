# Evaluation methodology

Authentication proves an identity; it does not authorize a referral action. The backend validates
OIDC tokens and enforces both role and organization scope on every sensitive operation.

## Trusted identity context

For local OIDC mode, the backend checks:

- JWT signature using the configured Keycloak JWKS endpoint;
- exact issuer and `medbot-api` audience;
- expiry and other standard token validity properties;
- realm roles mapped to MedBot's typed roles;
- a non-empty `organization_id` claim.

`organization_id` in a request body, query string, route, filename, or document is never trusted as
the caller's scope. The identity middleware constructs the authorization context once from validated
claims. Development-token mode exists for isolated tests, uses a server-owned token-to-identity map,
has no default identities, and requires explicit opt-in plus an explicit non-empty token map. It must
not be enabled in a deployed environment. OIDC is the fail-closed default; startup fails until its
issuer, audience, and JWKS URL are configured.

## Phase 1 role matrix

The table shows intended least privilege. Implemented endpoint dependencies are the authority if a
future documentation mismatch occurs and should be kept synchronized with this table.

| Capability | Intake specialist | Reviewer | Supervisor | Auditor | Administrator | Service |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Create referral and upload document | Yes | No | No | No | Yes | Yes |
| Read scoped referral metadata and document content | Yes | Yes | Yes | Yes | Yes | Yes |
| Start deterministic processing | Yes | No | No | No | Yes | Yes |
| Request information or supply an absent required field | No | Yes | Yes | No | Yes | No |
| Approve or correct extracted field | No | Yes | Yes | No | Yes | No |
| View completeness and FHIR preview | Yes | Yes | Yes | Yes | Yes | Yes |
| Write FHIR or route a ready referral | No | Yes | Yes | No | Yes | No |
| Read audit history | No | Yes | Yes | Yes | Yes | No |

The application `administrator` role does not administer Keycloak. Realm administration uses the
separate local bootstrap identity, which is outside application authorization. The `service` role
shares intake/write endpoints in Phase 1 so a non-interactive worker can run the synchronous
pipeline; it remains organization-scoped and cannot review, route, or read audit history.

An administrator role does not imply an undocumented cross-organization bypass. Any future
cross-organization support role requires an explicit policy, separate tests, and conspicuous audit.

## Resource checks

Authorization applies before returning or mutating:

- referral and document metadata;
- original document content or download locations;
- extracted fields, evidence, and review history;
- completeness/routing decisions and FHIR previews;
- workflow actions and audit history.

The database access pattern includes organization scope in resource lookup. A globally unique UUID is
not an access-control mechanism. Not-found responses may be used to avoid confirming the existence of
another organization's resource.

## Local realm

`infra/local/keycloak/medbot-realm.json` defines the six application roles, an API audience, an
organization claim mapper, PKCE for the browser client, and synthetic users in
`org-synthetic-alpha` and `org-synthetic-beta`. Passwords and the service client secret are obvious
local-only values. They are committed solely for deterministic local development and must never be
reused outside the Compose environment.

## Production responsibilities

The deployer must configure enterprise identity policy, TLS, MFA where appropriate, session/token
lifetime, revocation/offboarding, privileged access, access review, monitoring, and workforce
procedures. Keycloak `start-dev`, direct password grants, fixed users, and HTTP are not production
controls.

## Labeled-field grounding and packet conflicts

The current deterministic verifier supports the explicit referral labels in `field_grounding.py`.
It is intentionally conservative: a narrative quote cannot grant verified field meaning merely because
its text contains the proposed value. Real-model narrative proposals may retain a full-line literal
anchor for mandatory human review. Whitespace/case and the existing supported date formats are
normalized deterministically. This is labeled-field grounding, not general semantic or clinical validation.

The application independently scans all extracted blocks for conflicting values, even when the
provider selects only one candidate or omits the field. Distinct values produce `CONFLICTING` evidence
and a blocking issue. Equivalent supported date formats do not create a conflict. A reviewer must
record a valid corrected value and explanation. Verified source quotes are retained for inspection.

Reprocessing cannot silently reuse a machine field omitted from the new output. Human-supplied values
retain their separate provenance; the original proposal is not replaced on those fields. FHIR previews
exclude unsupported, conflicting, and unconfirmed model/OCR fields. See
[the document extraction boundary](../architecture/document-extraction.md) for real providers and limits.

## Executable synthetic workflow benchmark

`make benchmark` runs `scripts/evaluate_workflow.py` and writes `eval/reports/workflow-latest.json`
and `workflow-latest.md`. The command exits nonzero when an expected behavior fails. It complements
`make eval`, which retains its dataset-integrity behavior.

The benchmark executes the actual application services, database persistence, document extractor,
fixture AI provider, evidence verifier, policy, review/recovery actions, FHIR routing, and audit chain.
Each case uses an isolated in-memory SQLite database and local storage/FHIR adapters. No paid API,
network integration, or pytest fixture is required. Expectations come from the three committed fixture
oracles and seven separately authored cases in `eval/benchmark_cases.json`; predictions never update gold.

Measured outputs include:

- Micro accepted-field precision, recall, and F1. Only fields retained as verified/human-corrected count;
  expected fields intentionally exclude blocked conflicts or adversarial proposals. These are not raw
  proposal-quality metrics and do not establish OCR/model accuracy.
- False-ready rate, with the number of initially ineligible cases as denominator.
- Exact missing-item sets, processing state, expected rejection/conflict categories, blocked routing,
  recovery readiness, routing queue, idempotent request/supply/route retries, and audit-chain validity.
- Monotonic processing-only latency, sample count, median, and nearest-rank p95 for this small local run.

The report identifies Python/dependency versions, provider, extraction schema, policy version, and a
SHA-256 fingerprint of source, benchmark code, cases, and fixture bytes/oracles. It stores opaque case
IDs, counts, checks, and timing, without field values, document text, prompts, or responses.
Real-provider cost, confidence calibration, review time, generalization, fairness, clinical safety,
and production throughput remain unmeasured. Repeated local runs are useful regression checks, not
statistical evidence about real referral performance.

## Real-document benchmark

`make benchmark-real` executes `scripts/evaluate_documents.py` against the complete local Compose
stack. It refuses fixture AI and non-loopback API/identity URLs. Hosted evaluation additionally requires
`MEDBOT_ALLOW_HOSTED_EVAL=1`; no hosted credentials were used for the recorded local run.

Twelve committed synthetic PDFs cover compressed text, narrative sentences, multiple pages, clean and
noisy scans, missing DOB recovery, mixed native/scanned pages, prompt injection, conflicting values,
encryption, malformed input, and an unreadable scan. The versioned `eval/document_cases.json` oracle
records expected fields, source pages/vertical locations, raw text, conflicts, and errors independently
of predictions. `make generate-document-fixtures` rebuilds inputs and their oracle deterministically;
normal evaluation never rewrites gold. The cases are small regression fixtures, not a representative
healthcare dataset.

The executable measures:

- Micro proposal precision/recall against frozen administrative fields, before review. Conflicting DOB
  proposals may match either known candidate; an independent conflict assertion and explained correction
  are still mandatory. An omitted field lowers recall; an unexpected field lowers precision.
- Source-region recall: each expected field must have evidence on its independently specified page
  within 0.035 page height of the expected top. This is a coarse location regression, not box IoU or
  semantic evidence quality.
- Raw OCR word error rate from direct execution of the actual container's document extractor. Casefolded,
  whitespace-tokenized Levenshtein edits are divided by gold word count, before any model correction.
  WER includes all readable cases, including native text. Error-only cases do not contribute words.
- False-ready count before reviewer actions, blocked routing, expected failure categories, mandatory
  approvals, manual missing-DOB recovery, explained conflict correction, and actual FHIR-backed routing.

Reports identify provider/model, dependency versions, model digest where available, tested container
source hash, case/oracle/input fingerprint, timings, and aggregate counts. They omit document text,
field values, prompts, responses, and credentials. `eval/reports/documents-latest.json` is ignored by Git
and the command exits nonzero when any case fails.

The native local `qwen3.8:27b` run achieved 12/12 cases, proposal precision/recall and source-region recall
of 1.0, WER about 0.85%, and zero false-ready decisions. These results describe only this synthetic set
and model/runtime; the smaller Docker `qwen3:4b` convenience default is not claimed to match them.
Hosted OpenAI/Gemini adapters have executable SDK/HTTP contract coverage with synthetic in-memory
responses, not live quality measurements. Real records, handwriting, provider comparison, cost, latency
at scale, confidence calibration, and clinical safety remain unmeasured.
