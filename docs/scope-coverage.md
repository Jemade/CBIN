# Product scope coverage

Based on the CBIN Technical Product, Research and Delivery Scope, October 2026, version 0.1. The document describes a multi-phase programme; this commit provides the development foundation and keeps unfinished work visible.

| Area | Repository status | Next evidence or implementation |
| --- | --- | --- |
| Modular monolith and v1 REST API | Implemented | Load testing and gateway limits |
| Invoice/credit-note validation | Implemented, limited tax model | Test real Zimbabwe invoices and currency list |
| Identity directory | Manual evidence workflow | Verification partnerships, signed identities |
| Credentials, roles, tenant/environment isolation | Implemented | Independent security assessment |
| Idempotency and cross-connector duplicate detection | Implemented and tested | Vendor source reference stability evidence |
| Database, outbox and durable worker | Implemented | Backups, load/chaos testing and versioned migrations |
| Buyer review, acceptance, rejection and mapping | Implemented | Reusable SKU mapping, approval policies and variances |
| Ledger posting | Test simulation and Zoho prototype | Real sandbox path, totals/tax/FX reconciliation |
| Odoo native seller module | Prototype, cron disabled | Install in Odoo 18 sandbox, setup views, supported invoice shapes |
| Cloud accounting target | Zoho prototype | OAuth provisioning/refresh and pilot credentials |
| Offline operation | SQLite spool and Odoo local outbox | Signed envelopes, replay UI and outage field tests |
| Webhooks and event recovery | Implemented | Egress policy and receiver integration validation |
| Operations queue, attempts and replay | Implemented | Proactive alerts, support exports, connector heartbeat |
| Immutable audit | App append-only | Independent tamper evidence and retention |
| Digital proof of delivery | Pending | Signed POD contract, recipient workflow and mobile capture |
| Approval thresholds / PO variance | Pending | Research buyer rules and implement policy engine |
| PO, order confirmation, quotations, payments | Later phase | Versioned contracts and connector support |
| Legacy agents / fiscal-driver listeners | Later phase | Written vendor access authorization and working extraction proofs |
| Pharmacy / freight / property extensions | Later phase | Sector-specific validated contracts |
| Security review and four-week anchor pilot | Pending | Independent review and measured production evidence |

## Delivery order

1. Validate canonical invoices against real samples and install the Odoo prototype.
2. Configure a Zoho sandbox, OAuth lifecycle and buyer tax/currency mappings. Run the complete real-vendor path including timeout recovery. Do not enable live posting until this exit criterion passes.
3. Add Digital POD and buyer SKU reuse, then researched approval/variance policies.
4. Add schema migrations, observability/alerts, gateway controls, audit export, backup restore and production security review.
5. Onboard one authorized anchor seller/buyer pair and measure the four-week pilot.
6. Expand vendor/sector coverage only with discovery evidence and approved contracts.

## Expanded software discovery scope

The complete observed Fiscal Harmony catalogue is now tracked in [Fiscal Harmony coverage](fiscal-harmony-coverage.md): 34 entries, including separate editions and non-accounting products. Every entry can use the shared reviewed structured-import route. Native capture and buyer-posting integrations remain individually tracked and must pass real sandbox validation. The wider discovery scope does not remove the original Odoo/Zoho pilot gate.
