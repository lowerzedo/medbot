# ADR 0002: AI proposes; software verifies; policy decides

- Status: Accepted
- Date: 2026-08-23

## Context

Referral documents and model output are untrusted. Model confidence does not prove correctness, and
document content may contain prompt-injection instructions. Administrative workflow state,
completeness, and routing must be reproducible and auditable.

## Decision

AI providers return schema-validated field proposals with source-evidence references. Backend code
independently verifies the referenced document, page, block, hash, and quote. Versioned deterministic
rules decide completeness and queue routing. Model providers receive no workflow mutation, shell,
database, FHIR, object-storage, or arbitrary network tools. Human review is required when configured
evidence, confidence, conflict, or completeness conditions demand it.

## Consequences

- Provider/model changes do not change workflow authority.
- Prompt injection has no privilege-escalation route by design.
- Unsupported model values fail verification instead of silently entering the workflow.
- Policy changes require versioning and behavioral tests, while extraction quality can be evaluated
  separately.
