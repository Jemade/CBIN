# Connectors

## Test ledger

Set `CBIN_CONNECTOR_CONFIG='{"CBIN-DEMO-BUYER":{"type":"sandbox"}}'` in both API and worker. The deterministic `sandbox:` reference proves workflow/retry behavior only. It is not a vendor integration.

## Odoo 18 seller capture prototype

The native addon is in `addons/cbin_odoo`. It creates a durable Odoo outbox entry inside the seller invoice posting transaction. The cron is disabled on installation; enabling it requires a reviewed sandbox configuration.

1. Add the repository's `addons` directory to Odoo's addons path and install **CBIN Invoice Exchange** with the account module present.
2. Configure seller `res.company.cbin_business_id` and `cbin_tin`, and buyer `res.partner.cbin_business_id` and `cbin_tin` through administrator tooling. Dedicated setup views are still required.
3. Supply `CBIN_URL` (HTTPS) and `CBIN_API_KEY` in the Odoo server environment, under a secrets manager.
4. Create products with stable internal references, use a two-decimal currency and tax-exclusive percentage taxes. The prototype blocks discounts, multiple/group/fixed/inclusive taxes, missing SKUs or incompatible rounding.
5. Post a seller invoice. Inspect `cbin.outbound` through administrator tooling: pending means ready to transmit; blocked normalization does not interrupt the seller ledger.
6. Enable the CBIN scheduled action in the Odoo sandbox. The source invoice ID/database form a stable idempotency key. Connectivity loss retains the local record.
7. Confirm the normalized amounts and identities in CBIN. Fiscal data attachment and exchange-rate extraction require the deployment's actual fiscalisation/FX interfaces; the addon does not fabricate them.

The addon was syntax-checked, not installed against an Odoo server in this session. Odoo multi-company secrets, setup views, queue controls and support for refunds/discounts remain integration work. Never assume Odoo Online permits installation of arbitrary native modules. Target Odoo deployment and API plan must be confirmed.

Official reference: https://www.odoo.com/documentation/18.0/developer/reference/external_api.html

## Zoho Books buyer prototype

The worker's Zoho adapter uses the Bills API and external OAuth token provisioning. It performs exact reference lookup before a write, maps supplier/SKUs/account/tax/currency explicitly, checks a returned draft status and verifies returned grand total. Recovery fetches the matching bill and checks supplier, currency, total and draft state. Mismatches become reconciliation exceptions, never success.

Example shared API/worker configuration (placeholders are not usable credentials):

```json
{
  "CBIN-DEMO-BUYER": {
    "type": "zoho",
    "base_url": "https://www.zohoapis.com",
    "organization_id": "YOUR_SANDBOX_ORG",
    "token_env": "CBIN_ZOHO_BUYER_TOKEN",
    "currency_ids": {"USD": "YOUR_ZOHO_USD_ID"},
    "currency_exponents": {"USD": 2}
  }
}
```

Set `CBIN_ZOHO_BUYER_TOKEN` in the worker secret store. Acceptance must map tax rates to vendor IDs (e.g. `{"15":"YOUR_TAX_ID"}`). Region hosts are explicitly allowed. Automatic OAuth consent, refresh-token rotation, account capability detection and multi-currency FX behavior are not implemented. Canonical Decimal values convert at the vendor JSON boundary; reconciliation checks are required for provider rounding.

Live posting is disabled. Zoho credit-note posting is unsupported and fails explicitly. No QuickBooks connector is claimed. The cloud target selected for this repository is Zoho; switch targets only after a discovery decision.

Official reference: https://www.zoho.com/books/api/v3/bills/

## Generic offline spool

`OfflineSpool` in `cbin.connectors.offline` stores validated invoices in a SQLite WAL database, retaining stable keys across restarts. It replays in sequence and retains pending records on connectivity loss or uncertain acknowledgement. Permanent validation failures become blocked rows. Protect the spool volume with encryption and least-privilege filesystem permissions. A production signed-envelope protocol, retry scheduler, quarantine/replay tooling and installation distribution remain backlog items.

## Sandbox acceptance criteria

Run a real Odoo-to-CBIN-to-Zoho path with a buyer reviewer. Verify every field and tax amount, draft status, currency behavior, duplicate source events, lost response after bill creation, expiry/revocation of tokens, provider failures and manual reconciliation. Keep evidence and provider bill IDs. Unit tests with a mocked HTTP transport do not substitute for these checks.

## Odoo 18 buyer and portal capture adapter

`cbin.connectors.odoo.OdooAdapter` implements authenticated, company-scoped JSON-RPC reference lookup, posted seller invoice listing, addon export and draft vendor bill creation. Configure both API and worker with `examples/odoo-connector-config.json`, passwords in their named environment variables, and exact base URLs in `CBIN_ERP_ALLOWLIST`. Recovery checks the selected line products/accounts/taxes and invoice values, not just the grand total. HTTP transport contract tests pass; a real Odoo instance has not been exercised. Use [the two-machine acceptance procedure](TWO-MACHINE-PILOT.md) before certifying the adapter.
