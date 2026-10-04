# CBIN

Corebridge Business Interoperability Network is a vendor-neutral B2B transaction exchange. A seller submits a structured invoice from their existing software, a buyer reviews it, and a durable job queues the accepted document for a draft bill in the buyer's system.

**Status: runnable development MVP, not production-ready.** The test workflow runs end to end with an explicitly simulated ledger. Odoo capture and Zoho posting are integration prototypes requiring sandbox validation. CBIN does not replace a POS, accounting ledger, fiscal device or ZIMRA's tax channel.

## Included

- FastAPI v1 endpoints with bearer credentials, roles, environment and tenant isolation.
- Versioned invoice and linked credit-note contracts, integer minor-unit money and Decimal validation.
- Verified business directory, trusted operator onboarding, key issuance and revocation.
- Sender-scoped idempotency and source-reference deduplication across connectors.
- Immutable document payloads, buyer accept/reject decisions and complete event timelines.
- Database-backed transactional outbox, atomic leases, attempt history, backoff and dead letters.
- Lookup-only reconciliation for uncertain ledger writes; no blind posting retries.
- Signed, stable-ID webhook events, cursor-paginated recovery log and exact operator egress allowlist.
- Buyer portal and operator job view. Credentials stay in browser memory.
- Durable offline connector spool and an Odoo 18 outbound addon with cron disabled by default.
- Zoho Books draft bill adapter, tax/currency mappings and timeout recovery, gated to test mode.
- Catalogue of all 34 observed Fiscal Harmony entries, visible connector statuses and tenant-scoped reviewed JSON import mappings.
- PostgreSQL deployment configuration, SQLite local development, Docker and CI database matrix.

## Start locally

Python 3.12 or newer and Node.js 22 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
npm --prefix frontend ci
npm --prefix frontend run build
export CBIN_CREDENTIAL_PEPPER="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export CBIN_ENVIRONMENT=test
export CBIN_DATABASE_URL=sqlite:///./cbin.db
cbin init-db
cbin demo
export CBIN_CONNECTOR_CONFIG='{"CBIN-DEMO-BUYER":{"type":"sandbox"}}'
uvicorn cbin.api:create_app --factory --host 127.0.0.1 --port 8000
```

`cbin demo` requires an empty test database. It prints new seller, buyer and operator credentials once. Store the pepper securely; changing it invalidates existing credentials. Do not commit keys or paste them in issues.

In a second terminal activate the same environment, set the same database/environment/pepper/connector configuration, then run:

```bash
cbin-worker
```

Open **http://localhost:8000** for the portal and **http://localhost:8000/docs** for API documentation. Submit `examples/invoice.json` with the seller credential:

```bash
export CBIN_SELLER_KEY='your-generated-seller-key'
curl -X POST http://localhost:8000/v1/documents \
  -H "Authorization: Bearer $CBIN_SELLER_KEY" \
  -H 'Idempotency-Key: demo-invoice-001' \
  -H 'Content-Type: application/json' \
  --data-binary @examples/invoice.json
```

Connect the buyer credential in the portal. After the worker delivers the invoice, map `SKU-CABLE` to a buyer product reference, supply supplier/account references, then accept. Refresh after the worker processes posting. A `sandbox:` reference means **simulation only**, with no real ERP changes.

## Docker / PostgreSQL

```bash
export CBIN_DB_PASSWORD="$(python -c 'import secrets; print(secrets.token_hex(24))')"
export CBIN_CREDENTIAL_PEPPER="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export CBIN_CONNECTOR_CONFIG='{"CBIN-DEMO-BUYER":{"type":"sandbox"}}'
docker compose up --build -d
docker compose run --rm api cbin demo
```

The API binds to localhost. Provisioning is separate from startup; the migration service creates the initial schema. Future schema changes must use reviewed migrations, not `create_all`. PostgreSQL data uses a persistent volume. Configure managed backups and verify restores before a pilot.

## Checks

```bash
ruff check .
pytest -q
python -m compileall -q src addons
```

CI repeats tests against SQLite and PostgreSQL. To run the same suite against a **disposable** PostgreSQL database, set `CBIN_TEST_DATABASE_URL`. Tests drop and recreate its CBIN tables; never point this at real data.

## Documentation

- [Architecture and contracts](docs/architecture.md)
- [Connector setup and integration limits](docs/connectors.md)
- [All 34 observed Fiscal Harmony products and mapping setup](docs/fiscal-harmony-coverage.md)
- [Operations and security](docs/operations.md)
- [Scope coverage and delivery backlog](docs/scope-coverage.md)
- [Research and pilot evidence](docs/research.md)
- [Contributing](CONTRIBUTING.md) and [security reporting](SECURITY.md)

The original product scope sets a multi-phase programme. This repository establishes the executable foundation; live vendor validation, Digital POD, auto-approval, PO variance checks, advanced verticals and production assurance remain tracked work, not completed claims.

## TypeScript frontend

The portal is built with React, TypeScript and Vite in `frontend/`. Build it before starting the Python server:

```sh
cd frontend
npm ci
npm run build
cd ..
```

Docker builds the frontend automatically. For frontend development, run the API on port 8000 and `npm run dev` in `frontend`; Vite proxies API requests to the backend. The browser starts in an explicitly labelled sample workspace. Connect with an API credential to load tenant-scoped documents, inspect invoices, submit buyer decisions and view audit events. Credentials stay in memory only. Dashboard counts describe the current page, and software catalogue entries disclose prototype and validation status.

Design references: [Dribbble accounts payable operations](https://dribbble.com/shots/27640759-SparkOffice-AP-Account-Payables-Management-Dashboard), [Pinterest invoice dashboard](https://www.pinterest.com/pin/invoices-dashboard--545005992391488039/), and [Rara Business](https://rarathemes.com/wordpress-themes/rara-business/). These inform hierarchy, density, spacing and responsive structure; the frontend is an original implementation.

## Buyer bookkeeping

The buyer can search accounting records by name, allocate each invoice line to a purchase account, select tax codes and review a balanced proposed entry before approving a bill. Approved choices can be remembered for future invoices from that supplier. Optional purchase-order checks compare the supplier, currency and total. Sandbox records are labelled; the Zoho test adapter fetches real organization references. See [the bookkeeping guide](docs/BOOKKEEPING.md) for configuration, limitations, the additive database upgrade and full test commands.
