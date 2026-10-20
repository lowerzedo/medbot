# Shared responsibility

Compliance and security outcomes are shared. This project supplies reference software controls; it
does not operate the deployer's environment, workforce, vendors, policies, or legal program.

| Area | MedBot project | Deployment/platform owner | Healthcare organization/data controller | External provider/vendor |
| --- | --- | --- | --- | --- |
| Secure application design | Typed boundaries, validation, authorization hooks, audit model, tests | Configure supported versions and patch promptly | Approve use case and minimum-necessary workflows | Disclose security posture and material changes |
| Identity/access | Validate OIDC claims and enforce roles/scope | Integrate IdP, TLS, secrets, session settings | Provision/approve/review access; offboard users | Protect identity service and support revocation |
| Data protection | Avoid sensitive logs; opaque identifiers; storage/provider interfaces | Encryption, keys, networks, backups, retention implementation | Data classification, retention/disposal, disclosure policy | Contractual processing, region, retention, deletion controls |
| Audit/monitoring | Emit durable audit and safe telemetry | Protect/retain/export/alert; time sync and access controls | Review events and investigate anomalies | Supply relevant service audit/security evidence |
| Availability | Idempotent and explicit failure design | Capacity, HA, restore/DR tests, monitoring/on-call | Define RTO/RPO and downtime procedures | Meet contracted availability/recovery obligations |
| AI/OCR | Untrusted-provider boundary, evidence verification, deterministic policy | Select/configure provider and egress controls | Approve data use and human-review policy | Define training/retention/subprocessor/data-use terms |
| Incident response | Safe correlation IDs and integrity signals | Detect, contain, preserve evidence, recover | Coordinate privacy/security/legal response and notifications | Notify/assist under contract |
| Compliance determination | Document design intent and limitations | Produce configuration/operation evidence | Perform legal analysis, risk assessment, policies, training, and agreements | Provide attestations/agreements relevant to service |

## Before sensitive or production data

At minimum, the responsible parties should complete a deployment-specific data-flow and risk
analysis, contractual/BAA review where applicable, least-privilege design, TLS and secrets setup,
backup/restore test, incident exercise, vulnerability-management process, access review, audit
retention/monitoring plan, and validation of every enabled external provider.

The local fixed credentials, synthetic identities, development OIDC flow, and unauthenticated direct
HAPI/MinIO endpoints are deliberately outside that boundary.
