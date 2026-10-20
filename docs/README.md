# MedBot documentation

- Architecture: [system overview](architecture/system-overview.md),
  [data flow](architecture/data-flow.md),
  [document extraction/providers](architecture/document-extraction.md),
  [workflow states](architecture/workflow-state-machine.md), and
  [FHIR model](architecture/fhir-model.md)
- Security: [threat model](security/threat-model.md),
  [personal AI credentials](security/provider-credentials.md),
  [access control](security/access-control.md),
  [audit model](security/audit-model.md), and
  [AI security](security/ai-security.md)
- HIPAA-oriented engineering context: [technical control mapping](compliance/hipaa-control-mapping.md)
  and [shared responsibility](compliance/shared-responsibility.md)
- Evaluation: [methodology](evaluation/methodology.md) and [dataset](evaluation/dataset.md)
- Decisions: [ADR 0001](adr/0001-modular-monolith-and-repository-boundaries.md),
  [ADR 0002](adr/0002-verified-ai-and-deterministic-policy.md), and
  [ADR 0003](adr/0003-fhir-provider-boundary.md), and
  [ADR 0004](adr/0004-local-ocr-and-multi-provider-extraction.md), and
  [ADR 0005](adr/0005-personal-provider-credentials.md)

Documentation describes the implemented Phase 0/1 reference slice and calls out later-phase or
deployment responsibilities explicitly. It makes no compliance, profile-conformance, or clinical
claims.
