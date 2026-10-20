# Audit model

Application audit records are durable domain records, separate from logs, metrics, and traces. Logs
answer operational questions; audit records answer who performed which action on what scoped
resource, when, and in which causal chain.

## Record fields

Each record preserves:

- globally unique event ID and UTC timestamp;
- organization ID and referral ID;
- actor or service identity;
- action name;
- resource type and opaque resource ID;
- trace/correlation ID;
- bounded, non-sensitive metadata;
- previous event hash;
- event hash.

Patient names, birth dates, document/OCR text, source quotes, model prompts/responses, and complete
FHIR payloads are not safe audit metadata. Human field corrections are represented by purpose-built
review records; audit metadata can reference their opaque IDs.

## Hash-chain construction

For each referral/organization audit stream, the application serializes security-relevant fields in
a stable canonical order. The event hash is SHA-256 over the previous hash plus that canonical event
payload. The first record uses the defined genesis value. Verification recomputes the entire ordered
chain and fails on a missing, reordered, modified, or incorrectly linked record.

The hash chain is tamper-evident, not immutable. A database administrator with sufficient access can
rewrite records and recompute hashes. Production hardening should restrict write paths and may anchor
periodic chain heads in append-only/WORM storage or another independently controlled system.

## Event coverage

Phase 1 records referral creation, document upload/quarantine/duplicate detection, document and AI
extraction completion, evidence verification, field approval or correction, FHIR writes, routing,
and status changes. Completeness outcomes are reflected by the resulting audited status transition.
Failed attempts that matter for security or operations should be recorded safely without including
request bodies or sensitive values.

## Transaction and retry rules

- An audit record for a successful local state change is committed in the same database transaction
  where practical.
- A failed transaction must not leave an audit record claiming that the mutation succeeded.
- Retries use a stable logical action/idempotency key when duplicate events are possible.
- Chain updates serialize correctly for one stream; concurrent writers cannot use the same previous
  hash.
- Verification has behavioral tests, including mutation and deletion failures.

## Access and retention

Audit history is organization-scoped and restricted to roles with a business need. Export, retention,
legal hold, external archival, and monitoring are deployment/organizational policies, not established
by the local stack. A trace ID links audit and telemetry without copying sensitive payloads into
telemetry.
