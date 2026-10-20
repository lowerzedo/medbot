# MedBot

MedBot converts administrative referral PDFs into evidence-linked structured data and FHIR R4
workflow resources, detects configured missing information, and supports human-reviewed routing with
a tamper-evident audit history.

MedBot is not a diagnostic chatbot. It does not diagnose, recommend treatment, determine medical
necessity or eligibility, autonomously prioritize emergencies, or replace clinicians.

## What Phase 0 and Phase 1 include

- FastAPI, typed schemas, SQLAlchemy, Alembic, and PostgreSQL;
- PDF intake with bounded size/type/filename checks, opaque MinIO object keys, and hash verification
  whenever stored bytes are served or processed;
- local PDFium/Tesseract text and scan extraction, plus fixture, Ollama, OpenAI, and Gemini AI
  providers with schema validation and source-evidence verification;
- explicit workflow transitions, completeness policy, field approval/correction, missing-information
  recovery, conflicting-source review, and routing;
- FHIR R4 preview and idempotent HAPI FHIR writes with stable resource IDs;
- organization-scoped OIDC authorization using Keycloak roles and claims;
- durable application audit records with a verifiable SHA-256 hash chain;
- OpenTelemetry traces in Tempo and low-cardinality metrics in Prometheus/Grafana;
- deterministic workflow fixtures and twelve real-document benchmark cases with independent ground truth;
- fast CI checks, dependency/secret/misconfiguration scanning, and a separate integration workflow.

The trust model is deliberately simple:

```text
AI proposes -> software verifies -> deterministic policy decides
            -> human reviews when required -> system audits the action
```

## Repository layout

This repository owns backend, platform, synthetic-data, evaluation, and architecture/security
documentation. The separate sibling `medbot_ui` repository owns the Next.js reviewer experience and
uses this API over HTTP/OIDC. Neither repository imports runtime source from the other.

## Prerequisites

- Docker Engine with Docker Compose v2
- `make`
- Python 3.11 or later
- [`uv`](https://docs.astral.sh/uv/) for local Python checks and fixture generation
- `curl` for the health helper

Only synthetic data may be used with the local stack.

## Start the local platform

The committed defaults are obvious local-only values. An `.env` file is optional:

```sh
cp .env.example .env  # optional customization
make dev
```

`make dev` builds the API, starts dependencies, and waits for all HTTP health endpoints. It can take
several minutes the first time while HAPI FHIR initializes.

On macOS with Colima, start the Docker VM first. If its SSH readiness check hangs while an SSH
agent is active, `env -u SSH_AUTH_SOCK colima start --profile default` starts it without inheriting
that agent for this command; it does not change global SSH configuration.

| Service | Local URL | Purpose |
| --- | --- | --- |
| MedBot API/OpenAPI | <http://localhost:8000/docs> | Backend contract and workflow API |
| Keycloak | <http://localhost:8081> | Local OIDC realm `medbot` |
| MinIO console | <http://localhost:9001> | Local object-store inspection |
| HAPI FHIR | <http://localhost:8082/fhir/metadata> | Local FHIR R4 server |
| Prometheus | <http://localhost:9090> | Metrics queries and target status |
| Grafana | <http://localhost:3001> | Provisioned metrics and trace data sources |
| Tempo | <http://localhost:3200/ready> | Local trace backend readiness |

Run `make health` for a concise status table, `make logs` to follow service logs, and `make down` to
stop containers without deleting named volumes.

The Keycloak realm provides synthetic users in `org-synthetic-alpha` plus a second-organization
reviewer for isolation testing. The all-workflow demo user is `synthetic-admin-alpha` with password
`local-only-admin-password`. Grafana defaults to `local-admin` /
`local-only-grafana-password` unless `.env` overrides them. These credentials, direct password grant,
HTTP endpoints, exposed ports, and Keycloak `start-dev` are local development conveniences only.

The UI requests `openid`; the default `medbot-claims` client scope explicitly supplies the subject,
API audience, organization, and realm roles required by the backend. The realm import does not rely
on undeclared built-in client scopes. Keycloak imports a realm only when it does not already exist;
restarting containers does not update the stored realm configuration.

## Exercise the complete referral

For command-line testing, obtain a local access token from the public PKCE/demo client. Direct access
grants are enabled only in the synthetic realm:

```sh
curl --fail --silent --show-error \
  --request POST http://localhost:8081/realms/medbot/protocol/openid-connect/token \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode client_id=medbot-ui \
  --data-urlencode grant_type=password \
  --data-urlencode username=synthetic-admin-alpha \
  --data-urlencode password=local-only-admin-password
```

Use the returned `access_token` as `Authorization: Bearer <token>`, then follow the OpenAPI operations:

1. `POST /api/v1/referrals` with `{"specialty":"cardiology"}`.
2. `POST /api/v1/referrals/{id}/documents` as multipart form data with
   `classification=referral_order` and `synthetic/fixtures/complete-referral.pdf`.
3. `POST /api/v1/referrals/{id}/process`.
4. Inspect `GET /api/v1/referrals/{id}` and `GET /api/v1/referrals/{id}/fhir-preview`.
5. Approve or correct proposed fields when review is required, then
   `POST /api/v1/referrals/{id}/route`; routing writes stable FHIR resources before moving to
   `ROUTED`.
6. Inspect `GET /api/v1/referrals/{id}/audit`.

The incomplete fixture ends in `NEEDS_REVIEW`. The prompt-injection fixture remains complete because
its hostile sentences are inert document data, not workflow instructions.

## Development commands

| Command | Effect |
| --- | --- |
| `make format` | Apply Ruff formatting and safe lint fixes |
| `make lint` | Check formatting and lint rules |
| `make typecheck` | Run strict mypy checks |
| `make test` | Run fast tests excluding integration-marked tests |
| `make integration` | Run OIDC smoke, recovery, and real-document tests against the complete local platform |
| `make generate-fixtures` | Deterministically rebuild PDFs and ground truth |
| `make eval` | Emit Phase 1 dataset-integrity reports under `eval/reports/` |
| `make benchmark` | Execute the synthetic extraction, recovery, evidence, routing, and audit benchmark |
| `make dev-real` | Start local OCR and Ollama, download `qwen3:4b`, and configure the API for real extraction |
| `make benchmark-real` | Evaluate real OCR/model extraction, regions, review gates, recovery, and FHIR routing |
| `make generate-document-fixtures` | Rebuild the twelve synthetic document cases and their frozen oracle |
| `make compose-config` | Validate the resolved Compose model |
| `make ci` | Run the fast local CI-equivalent checks |

`make benchmark` executes the real application services with local fixture adapters against ten
frozen synthetic cases. It measures accepted-field accuracy, false-ready decisions, workflow recovery,
routing, retry behavior, audit integrity, and local timing. It does not evaluate a real model or OCR.

`make eval` does not execute a model and never invents benchmark values. Its extraction/workflow
quality fields are explicitly unavailable; see [the methodology](docs/evaluation/methodology.md).

## Real OCR and model extraction

Compose uses real PDFium/Tesseract extraction by default and fixture AI for quick offline development.
For a complete local model workflow, run `make dev-real`. Model weights require a download; inference
and OCR stay local. Use `make dev-real LOCAL_AI_MODEL=<installed-or-downloadable-local-model>` to choose
another model. CPU inference in a Docker VM can be slow; model size affects extraction quality.

To use an existing host Ollama installation with GPU support on macOS:

```sh
MEDBOT_AI_PROVIDER=ollama MEDBOT_AI_MODEL=<your-local-model> \
MEDBOT_OLLAMA_BASE_URL=http://host.docker.internal:11434/v1 \
docker compose up --build --detach api
make benchmark-real
```

For hosted BYOK, set `MEDBOT_AI_PROVIDER=openai` or `gemini`, an explicit `MEDBOT_AI_MODEL` supported
by that provider's structured-output API, and respectively `MEDBOT_OPENAI_API_KEY` or
`MEDBOT_GEMINI_API_KEY` in the backend environment, then recreate the API. Keep keys in your secret
manager or ignored local environment file. These are deployment defaults; users can override them with
personal settings below. No ambient cloud credential fallback or AWS call is involved.
Hosted mode sends extracted text to the selected
provider; the UI identifies this before intake. Live hosted evaluation requires the additional
explicit `MEDBOT_ALLOW_HOSTED_EVAL=1` opt-in and synthetic documents only.

All real-model and OCR proposals require reviewer confirmation, regardless of reported confidence.
The workspace shows the original page, highlighted source region, extraction method, and separate
OCR confidence. Missing values remain recoverable through the existing actions. See
[document extraction and provider decisions](docs/architecture/document-extraction.md) for configuration,
evidence guarantees, limits, and research sources.

## Personal AI settings

Open **Settings** in the UI to select Ollama, OpenAI, or Gemini and enter a model name. OpenAI/Gemini
require the user's own key. A blank replacement field keeps a saved key; removing it blocks new
personal hosted processing until a replacement or different provider is selected. “Use deployment
defaults” restores server configuration while retaining saved keys for later explicit use.

Settings are scoped by trusted organization and authenticated user, and apply to processing that user
starts. Keys are encrypted in PostgreSQL, never returned by the API, and never saved in browser storage.
Key inputs clear after a save attempt. Saving does not contact the provider or claim that a key works.
See [credential security and operations](docs/security/provider-credentials.md).

Compose creates a random master key once in the separate `provider-secrets` volume and mounts it
read-only into the non-root API. Outside Compose, configure `MEDBOT_PROVIDER_ENCRYPTION_KEY_FILE` or
`MEDBOT_PROVIDER_ENCRYPTION_KEY` with a Fernet key from your secret manager. Without one, personal
hosted-key storage fails closed; local Ollama remains available. Back up this key separately from
PostgreSQL. Losing it makes saved keys unreadable. Rotation requires controlled re-encryption or
users replacing their saved credentials.

Migration `20261020_0005` adds settings and durable settings audit events. Revision checks prevent
stale tabs overwriting newer settings. Existing local realms need `make provision-settings-test-user`
once to add the dedicated synthetic identity and register its administrator-managed organization
attribute; fresh realm imports include both. Tests use invalid synthetic hosted keys and local Ollama.

## Local platform details

All third-party images are pinned to explicit releases. PostgreSQL 18 uses its versioned data layout,
so its named volume mounts `/var/lib/postgresql`. MedBot, HAPI, and Keycloak use separate databases,
application-owned schemas, login credentials, and revoked public access on that local instance; the
bootstrap administrator is not supplied to an application. MinIO bucket initialization is
idempotent and enables versioning.

MinIO uses the committed public synthetic key in `infra/local/minio/synthetic-kms-key.txt` for local
server-side encryption. This fixture is not a production secret or an external KMS dependency.
Keep the same key while retaining the local data volume so existing objects remain readable.

The API applies Alembic migrations before serving requests. The recovery migration permits
reviewer-supplied fields with no AI value; the following migration adds a nullable FHIR attachment
digest. Existing documents acquire that digest when reprocessed, after size and SHA-256 verification.
Migration `20261020_0004` adds source regions, extraction method, and OCR confidence to stored text
blocks. Existing blocks retain their legacy viewer until new source regions are available.

`make integration` checks real OIDC role and organization boundaries, manual and supplemental recovery,
conflicting-source review, retries, persisted audit chains, source-byte integrity, and actual FHIR
resource persistence. It creates only synthetic records and does not delete existing volumes.

The HAPI JPA Starter publishes `hapiproject/hapi:v8.10.0-3`, which packages HAPI FHIR core 8.10.0.
The starter does not publish a `v8.10.1` image; Compose pins the real published revision. See the
[FHIR model](docs/architecture/fhir-model.md) for the conformance boundary.

Prometheus scrapes the API, collector, Keycloak, HAPI, and itself. Grafana provisions Prometheus and
Tempo data sources plus a small MedBot overview dashboard. The collector intentionally has no logs
pipeline: document text, prompts, model responses, patient data, and FHIR payloads do not belong in
normal logs or telemetry.

## Security and compliance boundary

The local stack is not production infrastructure. A real deployment requires TLS/private networks,
managed secrets and keys, production identity policy, malware scanning/sandboxed parsing, encrypted
backups and restore tests, high availability, provider contracts/data controls, incident response,
and a deployment-specific risk analysis.

MedBot is a HIPAA-oriented reference implementation, not a compliance product. It is not HIPAA
certified and cannot establish organizational compliance. Start with the
[system overview](docs/architecture/system-overview.md), [threat model](docs/security/threat-model.md),
[access-control model](docs/security/access-control.md), and
[shared-responsibility model](docs/compliance/shared-responsibility.md).

## Recover incomplete referrals

After processing an incomplete referral:

1. `POST /api/v1/referrals/{id}/request-information` records the current missing items in the durable
   audit chain and moves the referral to `NEEDS_INFORMATION`. It records follow-up intent only;
   it does not send a notification. Retrying while already waiting does not create another event.
2. A reviewer can `POST /api/v1/referrals/{id}/fields` with
   `{"field_path":"patient.date_of_birth","value":"1984-02-03","reason":"Confirmed with synthetic referrer"}`.
   Only an absent required field may be supplied. Invalid values, missing reasons, and existing fields
   are rejected. The response has `ai_value: null` and separate reviewer identity, time, value, and reason.
3. Alternatively, an intake user uploads a supplemental PDF through the existing document endpoint,
   then calls `/process` again on the same referral. New documents must be processed before routing.
4. Conflicting labeled source values block routing. A reviewer must use the field correction endpoint
   with a resolution reason; approving a conflicting field is rejected. The UI displays both sources.

Existing role boundaries apply: reviewers/supervisors/administrators request information and supply or
correct values; intake specialists/administrators/services upload and process documents. Every action
remains organization-scoped. Apply the new migration with `uv run alembic upgrade head` before running
against an existing PostgreSQL database.

## UI/API integration without Docker

For synthetic browser verification only, start an ephemeral API:

```sh
uv run --extra dev python scripts/run_synthetic_e2e_api.py
```

It binds only to `127.0.0.1:18000` and uses an in-memory database, object store, and FHIR adapter.
The sibling UI README documents the matching browser command. This exercises actual HTTP contracts
and the Next.js BFF, but does not replace the PostgreSQL/MinIO/Keycloak/HAPI Compose integration suite.
