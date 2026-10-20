# Threat model

## Scope and assumptions

This model covers the Phase 0/1 backend, local identity/storage/FHIR dependencies, the API contract
with `medbot_ui`, and provider boundaries. It assumes the host, container runtime, and developer
workstation are trusted for local development. Production changes that assumption and requires a
deployment-specific risk assessment.

Sensitive assets include referral documents, extracted data, reviewer corrections, organization
scope, identity tokens, storage/FHIR credentials, durable audit records, and backups. Availability
and audit integrity are also assets. The likely adversaries are unauthorized users, compromised
accounts/providers, malicious document senders, supply-chain attackers, and accidental operators.

## Threat register

| Asset | Threat | Attack path | Implemented/design mitigation | Residual risk and required follow-up |
| --- | --- | --- | --- | --- |
| Referral data | Unauthorized referral access | Guess opaque ID or call a read/action endpoint with an unrelated identity | Server derives organization from validated token and scopes every query; role checks are server-side | Application bugs remain possible; retain cross-organization tests and review every new query/action |
| Tenant boundary | Cross-tenant access | Client submits another `organization_id` or reuses an ID from a second organization | Client organization fields are not authorization input; trusted identity context controls scope | Privileged support workflows need a separately designed, audited elevation path |
| Host/parser | Malicious upload | Polyglot, malformed PDF, path filename, excessive size, or parser exploit | Size, safe filename, content type, signature checks, opaque object keys; parsing provider has no workflow authority | Phase 1 validation is not malware scanning or robust PDF sandboxing; add quarantine/scanning before real documents |
| Processing service | Document parser vulnerability | Crafted PDF exploits native/parser library | PDFium/Tesseract child process, page/pixel/text limits, deadlines, Linux CPU/memory/file limits, credential-free environment and parent-owned temporary cleanup | Same-user subprocess isolation is not a security sandbox; production needs patching, malware scanning, stronger sandboxing and adversarial corpus testing |
| Workflow authority | Prompt injection | Document tells a model to reveal data, call tools, or change state | Document text is data; model has no tools; schema/evidence/policy checks; committed hostile fixture | Provider-side logging/training policies and future tool additions require security review |
| Referral data | Compromised AI provider | Provider stores/returns sensitive or malicious content | Local fixture default or local Ollama; hosted BYOK requires explicit keys, UI identifies hosted transfer, model instrumentation disabled; proposals are untrusted and require review | Hosted provider requires BAA/data controls where applicable, minimization, retention review, egress policy, and incident plan |
| Clinical/admin accuracy | Hallucinated fields | Model returns plausible value absent from source | Every important value requires independently verified evidence; unsupported values are rejected/reviewed | OCR errors can create plausible source text; human review and extraction-quality evaluation remain necessary |
| Evidence integrity | Fabricated evidence | Model invents a block/page/hash/quote or cites a real quote for a different value | Verify document, page, block ID, block SHA-256, quote membership, and exact normalized value support | Hashes prove consistency with extracted text, not that OCR faithfully represented the image |
| Audit trail | Audit tampering | Update/delete/reorder database rows or forge a record | Append-oriented application API and previous-hash/event-hash chain with verifier | Database administrators can rewrite and recompute a chain; production needs restricted roles and immutable/off-site anchoring |
| Cloud/platform | Excessive IAM privileges | Broad role can read all objects/secrets or mutate infrastructure | Provider interfaces; production design requires least-privilege Terraform/IAM | Local Compose uses shared host trust; production IAM must be reviewed and tested |
| Identity | Stolen credentials/token | Phishing, browser/session theft, leaked local password, replay before expiry | OIDC validation, short-lived-token target, role/scope checks, brute-force protection in local realm | Local fixed credentials and HTTP are insecure; production needs TLS, MFA/SSO policy, secure sessions, revocation, and monitoring |
| Telemetry | Sensitive logs/traces/metrics | Code logs names, document text, prompts, FHIR payloads, URLs, or high-cardinality labels | Safe opaque identifiers only; no OTel logs pipeline; tests scan logs; bounded metric dimensions | Exception libraries/provider SDKs may leak bodies; review redaction and production exporter access |
| Stored data | Exposed backups | Public snapshot/bucket, unencrypted export, excessive retention | No production backup implementation is claimed; design calls for encryption, access controls, retention, and restore tests | Deployment owner must implement, inventory, test, and monitor backups |
| Source document | Insecure presigned URL | Long-lived/bearer URL leaks through browser history, logs, or referrer | Phase 1 serves through authorized backend paths and does not expose object-store credentials | Future presigned URLs need short TTL, scoped key, response headers, audit, and no URL logging |
| Source document | Object replacement or corruption | Stored bytes change after intake while database metadata retains the original digest | Verify byte length and SHA-256 against the trusted document row before every serve/process read | Production object versioning, retention locks, and integrity alerting remain deployment responsibilities |
| Workflow integrity | Replayed event/action | Retry or attacker repeats upload/FHIR/action request | Stable hashes/identifiers, explicit transitions, and idempotent FHIR writes | Every future asynchronous consumer needs idempotency keys, replay window, bounded retry, and duplicate tests |
| Workflow/storage | Duplicate processing | Worker/client retry creates duplicate resources or audit side effects | Unique constraints/stable identifiers and action semantics; retry behavior is tested | Race conditions under distributed workers require transaction/locking tests before queue introduction |
| Software supply chain | Compromised dependency/image/action | Malicious package, mutable image, poisoned CI action | Version-pinned images/dependencies, lockfile, Gitleaks, Trivy, dependency audit, minimal CI permissions | Tags are not immutable digests; production should verify signatures/digests and maintain patch SLAs |

## Privacy-specific abuse cases

- Search, error, and telemetry endpoints must not become alternate data-exfiltration channels.
- Object keys, trace IDs, and referral IDs are opaque but still treated as sensitive operational
  identifiers when correlated.
- Authorization applies to document download, FHIR preview, audit history, and review actions, not
  only referral list/detail endpoints.
- Support/debug access is not assumed. Any future privileged access requires purpose limitation,
  approval, short duration, and durable audit.

## Review triggers

Personal provider settings add a credential boundary: encrypted keys are scoped to organization and
user, authenticated ciphertext binds provider/principal, server master keys remain separate from the
database, stale writes are rejected, and missing/removed keys fail closed. Browser mutations enforce
origin checks; no API returns a key. The master key and process memory remain trusted operational
assets. See [provider credentials](provider-credentials.md) for tests, backup/rotation limits, and local
versus production responsibilities. The local organization attribute is explicitly administrator-managed.

Update this model when adding a real OCR/model provider, asynchronous events, presigned URLs, cloud
infrastructure, SMART/EHR connectivity, support access, new roles, or a new category of sensitive
data. Security findings outrank developer convenience.
