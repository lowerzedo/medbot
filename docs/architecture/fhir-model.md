# FHIR R4 model

MedBot uses FHIR as an interoperability representation, not as its operational workflow database.
A dedicated mapper converts validated domain data to FHIR R4 resources; HTTP transport and provider
details stay outside mapping rules.

## Initial referral representation

| Resource | MedBot meaning | Important linkage/idempotency rule |
| --- | --- | --- |
| `Patient` | Validated demographics explicitly present in the referral | Stable identifier derived from MedBot's opaque patient/referral context; never invent clinical data |
| `Practitioner` | Referring provider as stated and reviewed | Stable identifier; no inferred credentials or specialty |
| `Organization` | Referring/receiving organization where known and authorized | Identifier is organization-scoped |
| `ServiceRequest` | The explicitly requested service | References Patient and requester; no diagnosis, urgency, or medical-necessity inference |
| `DocumentReference` | Metadata and secure reference for the source document | References an opaque document identifier, hash, MIME type, and subject; raw object-store credentials are never exposed |
| `Task` | Administrative referral work item and queue state | Mirrors validated operational intent; MedBot remains workflow authority |
| `Provenance` | Who/what produced or reviewed the representation | Connects generated resources to source/reviewer activity |

`AuditEvent` and `Communication` may be exchanged later, but the Phase 1 durable audit chain is an
application record, not merely a FHIR `AuditEvent` or normal log.

## Bundle and identifier rules

- The mapper produces a transaction-typed bundle whose entries use stable `PUT` identities.
- One logical retry must resolve to the same logical FHIR resources.
- FHIR references are built after domain validation and cannot target an arbitrary host supplied by
  a document or client.
- A provider response is untrusted input and is schema/status checked before local persistence.
- Clinical facts absent from the source are omitted, not inferred or filled with defaults.
- Source/evidence records remain in the operational model even when selected values are represented
  in FHIR.

`DocumentReference.content.attachment.hash` contains the base64-encoded SHA-1 digest required by
FHIR R4. This interoperability metadata is separate from the SHA-256 digest used to verify stored
document integrity. Uploads record both digests. For legacy rows, the optional FHIR hash is omitted
until reprocessing computes it from verified source bytes; the migration does not read object storage.

## Local provider

The local stack uses the official HAPI FHIR JPA Starter image
[`hapiproject/hapi:v8.10.0-3`](https://hub.docker.com/r/hapiproject/hapi/tags). That starter revision
packages HAPI FHIR core 8.10.0; starter and core version numbers do not map one-to-one. There is no
published `v8.10.1` starter tag, so the Compose file pins the actual published revision instead of a
nonexistent image.

HAPI runs in R4 mode and stores its tables in a dedicated `hapi` PostgreSQL database/schema owned by
`hapi_local`. The MedBot API reaches it only through `MEDBOT_FHIR_BASE_URL`. The direct local HAPI
port is exposed for development inspection and is unauthenticated; it must not be exposed this way
in production.

## Conformance boundary

Phase 1 validates coherent R4 resource structure and local provider acceptance. It does not claim
US Core profile conformance, SMART compatibility, clinical terminology completeness, or production
EHR interoperability. Those claims require selected profiles, package installation, formal
validation, authorization scopes, discovery behavior, and conformance tests.

The Phase 1 HAPI adapter currently executes the stable `PUT` entries individually with bounded
retry, so a multi-resource write is idempotent but not atomic. A production adapter should submit and
validate the transaction bundle atomically when the target provider supports it, or implement
explicit compensation/recovery.
