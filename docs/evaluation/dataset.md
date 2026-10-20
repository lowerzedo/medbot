# Synthetic evaluation data

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
It is intentionally conservative: narrative quotes can anchor real-model proposals for human review,
but cannot grant verified field meaning on their own. Whitespace/case and supported date formats are
normalized deterministically. This is labeled-field grounding, not general semantic or clinical validation.

The application independently scans all extracted blocks for conflicting values, even when the
provider selects only one candidate or omits the field. Distinct values produce `CONFLICTING` evidence
and a blocking issue. Equivalent supported date formats do not create a conflict. A reviewer must
record a valid corrected value and explanation. Verified source quotes are retained for inspection.

Reprocessing cannot silently reuse a machine field omitted from the new output. Human-supplied values
retain their separate provenance; the original proposal is not replaced on those fields. FHIR previews
exclude unsupported, conflicting, and unconfirmed model/OCR fields. General document understanding is
not established by these synthetic fixtures.

## Real-document regression data

`synthetic/documents/` contains twelve artificial PDFs generated by
`synthetic/generator/generate_document_cases.py`: searchable/compressed and narrative text, multiple
pages, clean/noisy scans, absent DOB, scan prompt injection, mixed native/scan pages, conflicting DOBs,
encrypted input, malformed input, and an unreadable raster. Expected values, raw text, page positions,
alternate conflict locations, and expected errors live in `eval/document_cases.json`.

The generator fixes layout, PDF metadata, font, and noise seed. All names are explicit synthetic labels
and no real records are used. These small documents test behavior, not population representativeness.
`make generate-document-fixtures` regenerates them; `make benchmark-real` reads frozen gold without
modifying it. See [methodology](methodology.md#real-document-benchmark) for measurement definitions.

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

## Recovery and adversarial workflow cases

`eval/benchmark_cases.json` adds seven independently authored synthetic scenarios: manual recovery,
supplemental-document recovery, conflicting DOB resolution, wrong-field citation, truncated citation,
fabricated document evidence, and equivalent date formats. Its explicit input lines are rendered into
valid uncompressed synthetic PDFs at runtime; expected accepted fields, missing items, states, issue
codes, and recovery outcomes are authored separately from provider output. Together with the three
PDF fixtures these form the ten-case executable `make benchmark` suite.
