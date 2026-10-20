# ADR 0001: Modular backend with separate UI repository

- Status: Accepted
- Date: 2026-08-23

## Context

The referral workflow needs explicit domain, storage, identity, FHIR, AI-provider, and audit
boundaries, but Phase 0/1 has one team and one operational workflow. The reviewer UI has a different
runtime and release surface from the backend/platform.

## Decision

Keep `medbot` and `medbot_ui` as sibling Git repositories. Implement the backend as a modular
monolith and expose OpenAPI-described HTTPS endpoints as the only application contract with the UI.
Keep provider/repository interfaces only where replacement or external integration is real.

## Consequences

- Domain changes and transactions remain easy to understand and test in process.
- Frontend and backend can version independently without filesystem runtime coupling.
- Cross-repository contract changes require coordination and independent checks in both repos.
- A module may become a separately deployed service only after independent scaling, ownership,
  security, failure, or runtime requirements justify the operational cost.
