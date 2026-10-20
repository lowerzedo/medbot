# HIPAA-oriented technical control mapping

This is an engineering reference, not legal advice or a compliance determination. MedBot is not
"HIPAA certified," and running the software does not make a person, organization, or deployment
HIPAA compliant. Applicability and sufficiency depend on the entity, use case, risk analysis,
configuration, contracts, workforce, policies, and operations.

The references below use selected HIPAA Security Rule safeguard topics as organizing labels. A
covered entity or business associate must perform its own legal and risk analysis.

| Safeguard topic | MedBot technical support | Deployment/organizational evidence still required |
| --- | --- | --- |
| Risk analysis and management (45 CFR 164.308(a)(1)) | Threat model, secure defaults, dependency/secret scanning, explicit residual risks | Asset/data-flow inventory, formal risk analysis, treatment decisions, review cadence, accountable owners |
| Workforce security and access management (164.308(a)(3)-(4)) | Typed roles, organization scope, server-side authorization, audit events | Joiner/mover/leaver process, approvals, periodic access review, sanctions, emergency access procedure |
| Security incident procedures (164.308(a)(6)) | Safe operational telemetry, audit verification, explicit failure signals | Incident plan, triage/on-call, notification obligations, exercises, forensic preservation, lessons learned |
| Contingency planning (164.308(a)(7)) | Idempotent/retry-aware design; local reproducibility | Encrypted backups, restore tests, RTO/RPO, disaster recovery, emergency-mode operations |
| Evaluation (164.308(a)(8)) | Automated tests/scans and documented control boundaries | Periodic technical/nontechnical evaluation and material-change review |
| Facility/device/media controls (164.310) | No production physical controls are supplied by code | Datacenter/workstation/media policy, disposal, device management, physical access records |
| Unique user identification and access control (164.312(a)) | OIDC identities, role checks, organization scoping; no shared application identities by design | Production IdP, MFA/session policy, emergency access, account review, tenant configuration |
| Audit controls (164.312(b)) | Durable application audit events, trace correlation, tamper-evident hash chain | Retention, monitoring, review procedure, immutable export/anchoring, time synchronization |
| Integrity (164.312(c)) | Document/text hashes, evidence verification, schema validation, explicit transitions, audit chain | Database/storage controls, change management, key management, backup integrity, operator separation |
| Person/entity authentication (164.312(d)) | OIDC signature/issuer/audience/expiry validation | Identity proofing, authenticator policy, phishing resistance, credential recovery and revocation |
| Transmission security (164.312(e)) | HTTPS/OIDC production boundary is designed; external URLs are configuration | TLS termination, certificate lifecycle, service-to-service encryption, network controls, verification evidence |

## Minimum necessary and privacy engineering

MedBot supports least-privilege roles, organization-scoped access, purpose-specific APIs, and safe
telemetry. The deployer must decide who needs which referral fields, document access, audit access,
and support privileges. Data minimization, retention, disclosure accounting, patient rights, and
privacy notices depend on the deployment and applicable law/policy.

## Local stack caveat

Docker Compose uses HTTP, development-mode Keycloak, obvious fixed passwords, exposed ports, and
single-node storage. It is for synthetic data only and does not satisfy production safeguards.
