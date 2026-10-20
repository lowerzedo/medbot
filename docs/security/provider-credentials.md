# Personal AI credentials

The settings API uses organization and user IDs from trusted identity context only. Neither is accepted
in payloads. A composite primary key isolates both dimensions. Users can choose provider/model, but
cannot change workflow permissions, the OCR engine, provider endpoints, or the server-owned Ollama URL.

## Storage and usage

Keys use [Fernet authenticated encryption](https://cryptography.io/en/stable/fernet/) from cryptography,
now declared as a direct dependency. Each encrypted payload includes organization, user, and provider.
Decryption checks that context before use; moving ciphertext between principals or provider columns
fails. Only ciphertext is persisted. The master key comes from a separate backend secret file or explicit
environment variable, never from a default cloud credential chain. No AWS service is used.

During processing the backend resolves the initiating user's settings and decrypts only the chosen
provider's key into request-owned memory. A removed/unreadable personal key fails closed and does not
fall back to deployment credentials. Users without a personal choice keep deployment defaults.
Concurrent users do not mutate a shared provider instance.

Responses return configured flags and safe metadata, never credentials, ciphertext, prefixes, or
suffixes. Keys are excluded from model representations, logs, traces, audit events, and parser
environments. Newly entered keys pass through the authenticated same-origin BFF only for submission;
the password field clears after an attempt. No key is persisted in browser storage/cookies. Existing
HTTPS, secure sessions, and deployment access controls remain required outside synthetic local use.
Settings mutations use the BFF's origin check; direct backend calls require a bearer identity.
Saving settings does not contact a provider or assert credential/model validity.

## Actions and audit

`GET /api/v1/settings/ai` returns the caller's configuration and revision.
`POST /api/v1/settings/ai` selects provider/model and optionally replaces its key.
`POST /api/v1/settings/ai/remove-key` removes one saved provider key.
`POST /api/v1/settings/ai/use-defaults` clears the personal selection and retains saved keys.
Writes require the observed revision and lock the row; stale saves return 409. The primary key protects
concurrent initial inserts. Each mutation and its event commit together. Events record user,
organization, action, provider, time, and trace ID without secrets. These durable settings events are
separate from referral hash chains.

## Operations

Compose's networkless initializer creates one random key with exclusive file creation, owner-only
permissions and the API's fixed non-root UID. The API receives a read-only volume. Ordinary restarts
preserve the key. Back up and access-control the database and secret volumes separately. Docker
administrators still control the local host; this is synthetic local infrastructure, not a managed
production secrets service.

Outside Compose supply one of `MEDBOT_PROVIDER_ENCRYPTION_KEY_FILE` or
`MEDBOT_PROVIDER_ENCRYPTION_KEY`. Never commit it or reuse the public MinIO fixture key. Missing master
keys disable personal hosted storage; invalid configured keys fail startup. An unreadable/context-mismatched
ciphertext produces a safe replacement-key error. Removing a saved copy does not revoke the provider's
credential or remove historical database backups; follow deployment retention and vendor revocation rules.
Automated rotation is not implemented. Preserve the old key for controlled re-encryption, or remove
stored credentials and have users replace them after a deliberate master-key change.

The dedicated `synthetic-settings-alpha` identity isolates test credentials. The existing-realm helper
registers `organization_id` as administrator-view/edit only, preserving strict unmanaged-attribute policy.
It does not reset passwords or change other users. The exported profile is also pinned in the realm
fixture. Never let end users edit the organization attribute used for authorization.

## Verification

Unit tests cover ciphertext, write-only responses, scope/tamper checks, missing master key, same-org
user and cross-org isolation, stale saves, removal, and correct personal-provider resolution. Compose
checks inspect encrypted database bytes without printing them and exercise personal Ollama processing
through review and HAPI FHIR. Browser checks cover save, replacement, removal, reload, and local scan
processing. Hosted SDK contracts use synthetic in-memory HTTP responses; no paid hosted key is needed.
