# Architecture

CBIN starts as a modular Python monolith: HTTP edge, canonical contract, identity, document workflow, persistence, workers and connectors. Separate the API process from the worker process; both share the relational database.

## Flow

1. Authenticate a tenant-scoped credential for the server's test/live environment.
2. Validate integer money, Decimal quantities/rates, line-level half-up rounding and totals.
3. Match both parties to verified CBIN IDs and TINs. Verification is an operator evidence workflow, not an automated ZIMRA assertion.
4. In one transaction store the immutable invoice, sender-scoped idempotency mapping, audit events and routing outbox job.
5. A worker acknowledges delivery into the buyer portal. There is no external buyer-ERP acknowledgement at this stage.
6. The buyer approves with full SKU, supplier, account and tax mappings, or rejects with a reason. Conditional status updates prevent concurrent contradictory decisions.
7. Acceptance creates one unique posting job. A worker queries for an existing provider reference before creating a bill.
8. Successful posting records the provider reference and signed status events. An uncertain write stops at `needs_reconciliation`; lookup-only operator reconciliation may recover an existing draft but cannot create a second one.

## Transaction boundaries

Database uniqueness enforces `(environment, sender, idempotency key)`, `(environment, seller, document type + source reference)` and one buyer decision/job per document. Same body/key returns the existing document; changed bodies fail with stable conflict codes. Concurrency races return a retryable conflict. Connector-specific raw data never enters the stable contract.

All event/outbox mutations use the same transaction as the document transition. PostgreSQL job claims use `FOR UPDATE SKIP LOCKED`; SQLite uses a conditional compare-and-swap. Lease tokens fence stale completions. A process crash leaves a started attempt; stale ledger attempts are recovered by lookup and never treated as permission to repeat a write. Delivery and webhook replay are at least once. External consumers deduplicate by stable event ID.

## Contract decisions

- Canonical money uses minor units, never floats. Currency is a three-letter code; provider adapters must explicitly configure supported currency IDs and exponents. ISO-list enforcement remains a pilot task.
- Positive quantities, nonnegative values, tax-exclusive prices and line-level half-up rounding are supported. Discounts, tax-inclusive invoices and jurisdiction-specific tax adjustments need explicit schema extensions.
- `exchange_rate_zig` stores the provided snapshot; no live FX lookup or automatic conversion is performed. It is not used as an arbitrary destination-currency exchange rate.
- Credit notes link an invoice with the same seller, buyer and currency. Stored invoice amounts are never changed. Automatic original-invoice voiding/credit-balance reconciliation is not implemented.
- Fiscal metadata is supplied evidence only. `fiscal_verification` always reports `not_verified` or `not_supplied`.
- Audit history is append-only through the application. Database administrators can modify tables; external tamper-evident storage and retention enforcement are production work.

## API

OpenAPI is generated from the actual handlers at `/openapi.json`, with interactive docs at `/docs`.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/connectors` | Observed software catalogue and honest capability statuses |
| `POST /v1/connectors/{software_id}/documents` | Tenant/environment-scoped reviewed vendor JSON mapping and ingestion |
| `POST /v1/documents` | Canonical invoice/credit-note ingestion, mandatory Idempotency-Key |
| `GET /v1/documents` | Tenant-visible list and external-reference/status filtering |
| `GET /v1/documents/{id}` | Current status, immutable payload and audit timeline |
| `POST /v1/documents/{id}/accept` | Buyer decision and mapping |
| `POST /v1/documents/{id}/reject` | Buyer rejection with reason |
| `GET /v1/businesses/{id}` | Verified identity in the current environment |
| `POST /v1/webhook-endpoints` | Business-admin registration, operator-controlled dispatch |
| `GET /v1/events` | Tenant-visible cursor-paginated event recovery |
| `GET /v1/operations/jobs` | Operator job queue |
| `GET /v1/operations/metrics` | Operator queue state counts |
| `GET /v1/operations/jobs/{id}/attempts` | Persisted attempt outcomes |
| `POST /v1/operations/jobs/{id}/replay` | Audited dead-letter replay |
| `POST /v1/operations/jobs/{id}/reconcile` | Audited lookup-only recovery of ambiguous posting |

Stable errors include code, message, retryable flag and request ID. Document listing currently uses offset pagination; event recovery uses an opaque cursor. No payload-edit or audit-delete endpoint exists.
