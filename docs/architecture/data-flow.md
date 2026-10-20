# Referral data flow

This describes the implemented Phase 1 trust boundaries. Every transition is initiated by an
authenticated application action; neither document content nor model output can call workflow
operations directly.

## Happy path

1. The UI obtains an OIDC access token from Keycloak. The backend validates signature, issuer,
   audience, expiry, roles, and the `organization_id` claim.
2. An authorized intake user creates a referral. The organization scope comes from the validated
   identity, never from a client-selected tenant field.
3. The user uploads a PDF. The API enforces the configured size limit, safe filename rules,
   declared content type, and a minimal PDF signature check before storing it.
4. The API computes SHA-256, stores the bytes under an opaque object key in MinIO, and records a
   document row. Every later read verifies the stored size and hash before the bytes are served or
   processed. The original filename is metadata, not identity or a storage path.
5. PDFium reads searchable PDF lines and renders scanned pages for local Tesseract OCR. Lines retain
   page, hash, extraction method, confidence where available, and normalized source regions. Native
   parsing runs in a bounded subprocess; production malware scanning remains a deployment requirement.
6. The selected fixture or Pydantic AI provider (local Ollama, OpenAI, or Gemini) returns structured field
   proposals. Model output cites line indices; backend code supplies source IDs and hashes. The model
   cannot invoke tools or choose workflow state. Hosted text transfer requires explicit server config.
7. The backend independently verifies the document, page, text block, text hash, quote, and that the
   proposed value matches the quoted value. Unsupported or fabricated evidence is rejected.
8. Deterministic policy code checks required fields, field-specific formats such as date of birth,
   and document types. Missing or invalid information produces an explicit review/information state;
   it is never inferred from model confidence alone.
9. Real-model and OCR proposals require explicit reviewer approval/correction before entering trusted
   completeness and FHIR values. Missing information is recovered through audited application actions.
10. Validated fields are mapped to a coherent FHIR R4 bundle. The provider validates/persists it to
   HAPI FHIR using stable identifiers so retries do not create duplicate logical resources.
11. Deterministic routing records the policy version, validated inputs, resulting administrative
    queue, actor/service identity, and trace identifier.
12. Each important mutation appends a durable, hash-linked audit record in the same application
    transaction where practical. Normal logs and traces contain only safe operational identifiers.

## Stored representations

| Data | System of record | Handling rule |
| --- | --- | --- |
| Original document bytes | MinIO/object storage | Opaque key; never log content or put it in URLs |
| Operational referral state | MedBot PostgreSQL database | Organization-scoped queries and explicit transitions |
| Extracted fields/evidence | MedBot PostgreSQL database | Preserve source proposal and review disposition |
| Audit records | MedBot PostgreSQL database | Append-only application semantics and hash-chain verification |
| Interoperability resources | HAPI FHIR database via FHIR API | Stable identifiers and idempotent writes |
| Identity/session data | Keycloak database | OIDC; backend authorization remains authoritative |
| Metrics/traces | Prometheus/Tempo | Opaque IDs and bounded dimensions; no document text or prompts |

FHIR is not the operational database model. Object storage is not an authorization boundary. The UI
does not receive storage credentials and does not query HAPI or PostgreSQL directly.

## Failure and retry behavior

- Validation failures return structured, non-sensitive errors and do not begin downstream work.
- A storage or provider failure is visible as an explicit failed operation; exceptions are not
  swallowed.
- Retried document/FHIR operations use stable identifiers or idempotency keys.
- Invalid state transitions fail without mutating the referral.
- Failed audit verification is an integrity incident signal, not a debug warning.
- A prompt-injection sentence remains text in a source block; it cannot grant capabilities or
  override policy.

The Phase 1 processing path is synchronous and intentionally small. A future queue or workflow
engine requires an operational justification plus bounded retries, replay protection, dead-letter
handling, and idempotent consumers.
