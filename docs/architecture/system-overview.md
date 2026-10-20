# System overview

MedBot is an administrative referral-processing platform. It turns synthetic or authorized
referral documents into evidence-linked structured data, checks configured completeness rules,
maps validated data to FHIR R4 resources, and coordinates human-reviewed workflow actions. It is
not a diagnostic chatbot and does not provide diagnosis, treatment, clinical urgency, medical
necessity, or eligibility decisions.

## Repository boundary

The backend/platform repository (`medbot`) and reviewer UI repository (`medbot_ui`) are independent
sibling Git repositories. The UI consumes the backend over HTTPS using the documented API and OIDC
contracts. It never imports backend source or accesses PostgreSQL, MinIO, HAPI FHIR, or cloud
services directly.

The backend is a modular monolith and the primary policy/security boundary:

```text
Reviewer browser
      |
      | OIDC + HTTPS API
      v
medbot_ui -----> MedBot API
                    |
                    +--> PostgreSQL (operational state and durable audit)
                    +--> MinIO/S3-compatible object storage (source documents)
                    +--> HAPI FHIR through a provider boundary
                    +--> PDFium/Tesseract and fixture document extraction
                    +--> fixture/Ollama/OpenAI/Gemini AI providers
                    +--> OpenTelemetry Collector
```

## Phase 0 and Phase 1 components

| Component | Responsibility | Local implementation |
| --- | --- | --- |
| FastAPI application | API, authentication context, authorization, orchestration, safe errors | `api` on port 8000 |
| Domain/application modules | State transitions, evidence verification, completeness, routing, review, audit | In-process Python modules |
| Operational database | Referral, document, extraction, review, workflow, FHIR reference, and audit records | PostgreSQL 18.6 |
| Object storage | Immutable uploaded document bytes addressed by opaque keys | MinIO |
| Identity provider | OIDC tokens, local roles, trusted organization claim | Keycloak 26.7.1 |
| FHIR provider | FHIR R4 persistence and validation target | HAPI JPA Starter `v8.10.0-3` |
| Document extractor | Searchable PDF text, scan OCR, source regions, and page rendering | PDFium/Tesseract, or fixtures in fast tests |
| AI provider | Structured proposals; no tools or mutation capability | Fixture, local Ollama, or explicit OpenAI/Gemini BYOK through Pydantic AI |
| Telemetry pipeline | Low-cardinality metrics and spans from major workflow boundaries | OpenTelemetry Collector, Prometheus, Tempo, Grafana |

PostgreSQL hosts separate databases and application-owned schemas/logins for MedBot, HAPI FHIR, and
Keycloak. The bootstrap administrator owns the databases but is not provided to any application;
public connection/schema privileges are revoked. This is economical for local development while
preventing an application credential from reaching another component's database. Production
deployments should use separately managed credentials and may use separate instances where
availability or security boundaries require them.

## Trust and decision model

```text
Untrusted document -> AI proposes -> software verifies evidence and schema
                  -> deterministic policy decides -> human reviews when required
                  -> application performs action -> durable audit records the result
```

Document bytes, extracted text, model output, FHIR responses, and client requests are untrusted at
their boundaries. The AI provider has no shell, network, database, object-storage, FHIR, or workflow
mutation tools. Only application services can request validated state transitions and external
writes.

All real-model and OCR proposals require explicit human review before becoming trusted workflow/FHIR
values. See [document extraction](document-extraction.md) for provider research, configuration, source
geometry, and resource/failure limits.

## Local-only versus production boundaries

Docker Compose is a development environment. It intentionally uses HTTP, fixed synthetic users,
obvious local-only passwords, exposed service ports, Keycloak development mode, and a single-node
storage topology. None of those defaults are suitable for production. Production requires managed
secrets, TLS, private networking, backup/restore procedures, availability design, malware scanning,
vendor agreements, operational monitoring, and organization-specific policy and risk analysis.

See [ADR 0001](../adr/0001-modular-monolith-and-repository-boundaries.md) for the deployment and
repository decision, and [the threat model](../security/threat-model.md) for residual risks.
