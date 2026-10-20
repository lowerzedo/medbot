# ADR 0003: Provider-neutral FHIR mapping with HAPI locally

- Status: Accepted
- Date: 2026-08-23

## Context

Local contributors need a complete FHIR R4 workflow without cloud credentials. A later production
deployment may use another FHIR server such as AWS HealthLake. Domain logic must not depend on a
specific HTTP client or provider's storage schema.

## Decision

Keep FHIR mapping separate from FHIR transport behind a provider interface. Use HAPI FHIR JPA in the
local Compose stack, backed by its own PostgreSQL database/schema. Generate stable resource
identifiers and make provider writes idempotent.

## Consequences

- Mapping tests can run without a network service.
- Local integration tests exercise a real R4 server.
- Replacing HAPI requires an adapter and conformance tests, not changes to workflow policy.
- Phase 1 does not imply US Core or SMART conformance; those require explicit profile and
  authorization work.
