# Operations and security

## Onboarding and keys

Host-level CLI access is a trusted operator boundary. Protect the host/database and do not expose CLI commands over HTTP. A new business starts unverified:

```bash
cbin add-business CBIN-BUS-001 --tin YOUR_TIN --name 'Business legal name' --registration YOUR_REGISTRATION
cbin verify-business CBIN-BUS-001 --evidence INTERNAL_EVIDENCE_REFERENCE --operator OPERATOR_ID
cbin issue-key CBIN-BUS-001 --role admin
cbin revoke-key CREDENTIAL_ID --operator OPERATOR_ID
```

Issue a new key before revoking an old one to rotate. HMAC-SHA256 credential digests are stored; raw keys are printed once. The pepper must be managed as a secret. The roles are submitter, reviewer, business admin and environment-wide operator. Operator keys have broad document visibility; restrict issuance and keep them away from buyer users. CLI operator IDs are recorded host-level identity labels, not an independent remote authentication mechanism.

The application enforces environment scoping even in a shared development database. Production must use separate test/live databases, deployments, hosts and secrets. Business directory lookup exposes only verified business details to authenticated users in that environment.

## Job recovery

`pending → running → completed` is the normal path. Recoverable failures return to pending with bounded jittered backoff. Six failed attempts produce dead letter. Attempts and outcomes are persistent. Inspect operator jobs, attempts and metrics, correct the cause and replay with a reason. Posting remains accepted while unavailable; no ledger success is invented.

For an uncertain posting, use `/v1/operations/jobs/{id}/reconcile` with an operator credential and a reason. This queues lookup-only reconciliation. If no matching provider bill is found, the job remains unresolved; an operator must investigate provider processing before any approved new write strategy. There is intentionally no force-create endpoint.

Workers handle SIGTERM/SIGINT by finishing their current job and stopping. Allow a termination grace period longer than provider timeout and database finalization. A hard kill is recovered by lease expiry. One worker process executes one external request at a time; limits/circuit breakers across multiple workers remain pilot engineering.

## Webhooks

Business admins register HTTPS endpoints with an environment secret reference. Operators supply `CBIN_WEBHOOK_ALLOWLIST` as an exact URL JSON array and the referenced `CBIN_WEBHOOK_*` secret, at least 32 characters, in worker secrets. Registration alone grants no outbound network access. Redirects are disabled.

Deploy network egress policies blocking loopback, link-local, metadata and private ranges and permitting approved receivers only. Exact URL configuration is not a substitute for DNS-rebinding protection. Restrict operator-managed URLs and resolver behavior.

Headers: `X-CBIN-Event-ID`, `X-CBIN-Timestamp`, `X-CBIN-Signature`. Signature is `v1=` plus the hex HMAC-SHA256 of `timestamp + '.' + exact raw body`. Verify constant-time, allow at most 300 seconds clock skew, then durably deduplicate by event ID. The helper `verify_event` verifies signatures/window only; receivers own persistent event deduplication. Requests are at least once. Events and webhook jobs commit together.

## Before a live pilot

- TLS ingress, credential/IP rate limits, request size limits and network egress controls. The application enforces 2 MB bodies; distributed throttling belongs at the gateway and is not included here.
- Managed PostgreSQL, encryption at rest, secrets manager, backups, recovery targets and a successful restore exercise.
- Reviewed schema migration process, provider idempotency proof, vendor authorization and consent records.
- Full legal/data protection review. CBIN does not assert regulatory compliance or official tax-signature verification.
- Security review and penetration test, protected branches/required checks and least-privilege service accounts.
- Request/tenant trace logging without raw secrets or invoice PII, numeric SLOs, queue/dead-letter/ambiguity alerts and on-call/runbooks.
- Independent append-only audit export, retention controls and customer data export/deletion policies.

No live deployment, security audit, penetration test or vendor approval is implied by this repository.
