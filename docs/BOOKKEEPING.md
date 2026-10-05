# Buyer bookkeeping review

The buyer opens a delivered invoice, searches its accounting system’s suppliers, assigns each line to an accounting item and purchase account, selects a matching tax code, and reviews a proposed allocation before approval. Changing any accounting selection invalidates the displayed preview. The server validates references, tax rates and complete line coverage again during approval.

Approval records the decision and queues a bill. It does not record a supplier payment. Accounting software remains responsible for the final ledger and tax treatment. Zoho posting currently creates a draft bill in the test-gated adapter; production use still needs vendor validation.

## Make the next invoice easier

“Remember these choices” saves the approved supplier, item, tax and account mappings on the server, scoped to the buyer, seller, environment and accounting connection. Unknown items remain unfilled. Removed records are not suggested. An item allocated to different accounts on the same invoice does not receive an automatic account suggestion. Purchase orders are never reused automatically. Changing accounting connections invalidates previous suggestions.

The user can select a different account for each line, covering expenses, inventory or assets where available in the connected chart of accounts. The portal does not offer department/project allocation yet.

## Accounting reference sources

- Sandbox: explicitly labelled simulation records, available only in the test environment.
- Zoho Books: active suppliers, eligible purchase accounts, active items and tax records fetched from the configured organization. Lists are paginated. The portal never substitutes demo records when a real lookup fails.
- Odoo 18: active suppliers, products, eligible company accounts and percentage purchase taxes from the configured test database. Creates and reconciles draft vendor bills; no purchase-order lookup. See [the pilot setup](TWO-MACHINE-PILOT.md).
- Other catalogue entries: accounting reference lookup is not implemented yet.

References are cached for 15 minutes, scoped to the buyer and connection. “Refresh records” requests an updated snapshot. Zoho needs OAuth read permissions for contacts, chart of accounts, items and settings; purchase-order read access is needed when the optional lookup is enabled. Existing bill creation/read permissions are still required for posting and recovery.

Example test configuration:

```json
{
  "CBIN-DEMO-BUYER": {
    "type": "zoho",
    "token_env": "CBIN_ZOHO_TOKEN",
    "organization_id": "YOUR_TEST_ORGANIZATION",
    "currency_ids": {"USD": "YOUR_USD_CURRENCY_ID"},
    "currency_exponents": {"USD": 2},
    "purchase_order_lookup": true
  }
}
```

Store this JSON in `CBIN_CONNECTOR_CONFIG`, and provision the token separately in its named environment variable. Tokens do not enter the browser or the reference cache. Regions and production posting remain gated by the adapter registry.

## Purchase-order check

The optional check compares supplier, currency and invoice total with the selected order. It does not compare quantities, confirm goods receipt, reserve outstanding balances, or prevent a second invoice from billing the same order. Mismatches block the matched-order flow; remove the selection only after independently reviewing the discrepancy. The Zoho draft bill stores the checked order reference in its notes; it does not automatically consume purchase-order lines.

## Existing database upgrade

With the existing database/environment/pepper configured, run:

```sh
cbin migrate-mvp
```

This additive, repeatable command creates the two bookkeeping tables and tenant document-page indexes. It does not drop or modify existing documents. Fresh databases include the tables through `cbin init-db`.

## Run the full checks

From the repository root after installing the Python development dependencies:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
ruff check .
pytest -q
python -m compileall -q src addons
cd frontend
npx playwright install --with-deps chromium
npm run test:e2e
```

Activate your Python virtual environment before the browser tests, or set `CBIN_TEST_PYTHON` to its Python executable. The browser runner starts a disposable loopback-only FastAPI server and SQLite database. Its testing endpoints exist only in `tests/e2e_server.py`; they are not part of the production application. Tests cover desktop and mobile approval, draft preview invalidation, posting, saved mapping reuse, rejection, unavailable lookups and browser credential storage.

CI runs the backend suite against SQLite and PostgreSQL, then runs the browser suite and Docker build on the SQLite job. Provider contract tests mock Zoho HTTP responses. Passing these checks does not substitute for running against an actual Zoho test organization.

Reference documentation: [Zoho contacts](https://www.zoho.com/books/api/v3/contacts/), [chart of accounts](https://www.zoho.com/books/api/v3/chart-of-accounts/), [items](https://www.zoho.com/books/api/v3/items/), [taxes](https://www.zoho.com/books/api/v3/taxes/), [purchase orders](https://www.zoho.com/books/api/v3/purchase-order/), [bills](https://www.zoho.com/books/api/v3/bills/), and [Playwright web server](https://playwright.dev/docs/test-webserver).

The supplier/item/account/tax fields open browseable lists on click. **Show more records** exposes additional entries without typing; searching remains optional. Login/setup credentials are entered once per session, but invoice fields and bookkeeping references do not require typing.
