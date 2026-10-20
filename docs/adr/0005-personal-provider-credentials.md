# ADR 0005: Personal AI settings with backend encrypted credentials

- Status: Accepted
- Date: 2026-10-20

## Context

Users need their own provider/model choices and API keys without sharing another user's credentials,
altering deployment-wide settings, or exposing saved keys to browser reads. Local development must
remain independent of paid services and AWS credentials.

## Decision

Store settings under the trusted organization/user pair and resolve them for the actor initiating
processing. Keep provider endpoints and Ollama server addresses administrator-owned. Encrypt OpenAI
and Gemini keys with Fernet, including principal/provider context in the authenticated plaintext.
Supply the master key through a separate backend secret. In Compose a networkless initializer creates
one persistent owner-only key volume; the API mounts it read-only as a fixed non-root UID.

Settings APIs are write-only for credentials. A blank replacement retains the key; removing it fails
closed for personal hosted processing. Users can explicitly return to deployment defaults. Revisions
and row locks protect concurrent updates. Successful changes create durable safe settings audit events
in the same transaction. Existing AI/schema/evidence/review/workflow boundaries remain in force.

## Consequences

- The browser handles newly typed credentials only for submission; saved keys never return to it.
- Database backups alone cannot decrypt keys. Operators must separately back up/protect the master
  key and plan rotation/re-encryption or credential replacement. Automated rotation is not implemented.
- Saving does not make paid validation calls or claim credential validity. Local Ollama provides the
  live E2E path; hosted adapters retain synthetic HTTP contract tests.
- No AWS service is introduced. Production secret delivery, retention, TLS, and provider agreements
  remain deployment responsibilities. The local setup is documented for synthetic use only.
