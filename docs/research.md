# Research and pilot evidence

Engineering code cannot confirm vendor access, customer demand, tax-signature rules or legal authorization. Keep field evidence in an access-controlled workspace, not the public repository.

## Six-step discovery gate

1. Landscape: software versions in use, integration partners, invoice handling baseline.
2. Technical audits: demonstrate a supported extraction method on Odoo and the selected cloud target.
3. Stakeholders: confirm data ownership, API limits/licences, fiscal metadata access and buyer identity rules.
4. Interview at least 20 buyers/sellers: record manual effort, approval rules, tax/currency exceptions, SKU matching and outages.
5. Synthesis: validate the canonical contract against redacted invoices; identify incompatibilities before widening support.
6. Go/no-go: choose anchor clients and connector versions, approve a fixed pilot scope and document unresolved risks.

## Compatibility matrix template

| System/version | Owner authorization | Extraction proof | Buyer identity fields | Offline behavior | Tax/FX cases | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| Odoo target deployment | Pending | Pending | Pending | Pending | Pending | Pending |
| Zoho sandbox | Pending | Pending | Pending | Cloud/API retry | Pending | Pending |
| Legacy target (future) | Pending | Pending | Pending | Pending | Pending | Deferred |

## Pilot record per document

Use a redacted source reference, CBIN ID, seller/buyer identity evidence references, captured/received/delivered/decision/provider timestamps, currency/tax/total comparisons, provider draft reference, retry count and any operator exception. Do not put TIN scans, credentials or production invoice bodies in GitHub.

## Exit measures

- Duplicate source events, connector reconnects and uncertain responses do not produce duplicate bills.
- Buyer verifies a draft with zero re-keying and matches all tax/currency amounts.
- Every outcome has an exportable event trail and every failure has an explained recovery.
- Review time below one minute against a measured manual baseline, not an assumed claim.
- One anchor client runs live documents for four weeks without unresolved data errors, after live approval.
