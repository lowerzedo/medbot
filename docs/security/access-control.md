# Access control

Authentication proves an identity; it does not authorize a referral action. The backend validates
OIDC tokens and enforces both role and organization scope on every sensitive operation.

## Trusted identity context

For local OIDC mode, the backend checks:

- JWT signature using the configured Keycloak JWKS endpoint;
- exact issuer and `medbot-api` audience;
- expiry and other standard token validity properties;
- realm roles mapped to MedBot's typed roles;
- a non-empty `organization_id` claim.

JWT time validation uses a fixed five-second clock-skew tolerance for `iat`, `nbf`, and `exp`.
Immediate requests after Keycloak login previously failed intermittently with
`ImmatureSignatureError` when a signed token's issued-at second was ahead of the API clock.
This small tolerance handles that boundary without disabling time checks; tokens issued/not valid
more than five seconds ahead, or expired by at least five seconds, are rejected. Signature, exact
issuer/audience, required claims, roles, and organization validation remain mandatory.
The policy uses PyJWT's [documented leeway](https://pyjwt.readthedocs.io/en/stable/api.html).

Rejection logs contain only the validation exception class, never the token, claims, or provider
error text. The client still receives the generic authentication-required response.

`organization_id` in a request body, query string, route, filename, or document is never trusted as
the caller's scope. The identity middleware constructs the authorization context once from validated
claims. Development-token mode exists for isolated tests, uses a server-owned token-to-identity map,
has no default identities, and requires explicit opt-in plus an explicit non-empty token map. It must
not be enabled in a deployed environment. OIDC is the fail-closed default; startup fails until its
issuer, audience, and JWKS URL are configured.

## Phase 1 role matrix

The table shows intended least privilege. Implemented endpoint dependencies are the authority if a
future documentation mismatch occurs and should be kept synchronized with this table.

| Capability | Intake specialist | Reviewer | Supervisor | Auditor | Administrator | Service |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Create referral and upload document | Yes | No | No | No | Yes | Yes |
| Read scoped referral metadata and document content | Yes | Yes | Yes | Yes | Yes | Yes |
| Start deterministic processing | Yes | No | No | No | Yes | Yes |
| Request information or supply an absent required field | No | Yes | Yes | No | Yes | No |
| Approve or correct extracted field | No | Yes | Yes | No | Yes | No |
| View completeness and FHIR preview | Yes | Yes | Yes | Yes | Yes | Yes |
| Write FHIR or route a ready referral | No | Yes | Yes | No | Yes | No |
| Read audit history | No | Yes | Yes | Yes | Yes | No |

The application `administrator` role does not administer Keycloak. Realm administration uses the
separate local bootstrap identity, which is outside application authorization. The `service` role
shares intake/write endpoints in Phase 1 so a non-interactive worker can run the synchronous
pipeline; it remains organization-scoped and cannot review, route, or read audit history.

An administrator role does not imply an undocumented cross-organization bypass. Any future
cross-organization support role requires an explicit policy, separate tests, and conspicuous audit.

## Resource checks

Authorization applies before returning or mutating:

- referral and document metadata;
- original document content or download locations;
- extracted fields, evidence, and review history;
- completeness/routing decisions and FHIR previews;
- workflow actions and audit history.

The database access pattern includes organization scope in resource lookup. A globally unique UUID is
not an access-control mechanism. Not-found responses may be used to avoid confirming the existence of
another organization's resource.

## Local realm

`infra/local/keycloak/medbot-realm.json` defines the six application roles, an API audience, an
organization claim mapper, PKCE for the browser client, and synthetic users in
`org-synthetic-alpha` and `org-synthetic-beta`. Passwords and the service client secret are obvious
local-only values. They are committed solely for deterministic local development and must never be
reused outside the Compose environment.

## Production responsibilities

The deployer must configure enterprise identity policy, TLS, MFA where appropriate, session/token
lifetime, revocation/offboarding, privileged access, access review, monitoring, and workforce
procedures. Keycloak `start-dev`, direct password grants, fixed users, and HTTP are not production
controls.
