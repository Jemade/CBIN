# Invoice delivery, Epson printing and buyer records

## Implemented flow

1. The seller POS/ERP sends `POST /v1/seller/invoice-packages` with a canonical invoice and its original PDF, PNG or JPEG. The usual seller credential and Idempotency-Key are required. The package is committed atomically: malformed evidence does not leave a partially delivered invoice. Repeating the package requires the same original bytes. The previous `/v1/documents` endpoint remains available for data-only integrations.
2. CBIN verifies registered party IDs/TINs and line-level totals, creates its own labelled exchange PDF and stores the canonical JSON separately. This validates the exchange protocol, not ZIMRA compliance.
3. The outbox delivers the invoice to the matched buyer's inbox. Buyer-facing files retain provenance (`seller_original`, `buyer_scan`, `exchange_copy`, `canonical`). The original is never replaced by a recreated invoice.
4. The buyer selects their own supplier, stock item, account and tax configuration for every invoice line, reviews the allocation and approves. Supplier mappings remain reusable. Partial invoice lines are not silently dropped. A purchase-order header match is not goods-received confirmation.
5. The worker creates a draft bill, verifies provider acknowledgement and stores the ERP reference. `posted` is the legacy protocol state; the interface calls this **Draft bill created**. It does not mean the journal is posted or payment is made.
6. Separate attachment jobs move evidence into the ERP. Failures do not repeat bill creation. Changed connector settings stop attachment delivery; uncertain attachment writes require lookup-only reconciliation. A matching ERP bill is checked before files are attached.
7. ERP posting, VAT treatment, inventory valuation, goods receipt, payment and bank reconciliation remain controlled by the buyer's ERP. CBIN does not make payment or final ledger-posting calls.

## Epson and PDF output

The document view provides **A4 copy**, **Receipt copy** and, for sellers, **Epson print file**. Download requests use the in-memory business credential; credentials are never put in download URLs. The A4 and 80 mm PDF layouts preserve invoice details, totals, supplied party information and fiscal verification details. Long receipts paginate. Generated copies are labelled as exchange copies and do not manufacture a valid fiscal invoice.

The ESC/POS output supports 32- and 48-column text, safe text wrapping, QR model 2 and cutting. ESC/POS control characters supplied in product descriptions cannot inject printer commands. QR data is restricted to supplied HTTPS links at `fdms.zimra.co.zw`. A printed QR code is not itself a successful fiscal check.

On the Ubuntu seller machine, install the correct Epson/CUPS printer queue, download `invoice-escpos48.bin`, and run:

```sh
cbin-print invoice-escpos48.bin --queue EPSON_TM_T20
```

Use the actual installed queue name. The command queues a file locally; only a paper inspection on the actual printer confirms printing. Verify model support, paper width, characters, cutting and QR readability. Windows can print the downloaded PDF using its Epson driver; direct raw Windows printing is not implemented.

Reference layouts: [ZIMRA section 10](https://www.zimra.co.zw/downloads/category/9-domestic-taxes?download=3807:fiscalisation-apidocumentation), [Epson ESC/POS QR commands](https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/gs_lparen_lk.html). The deployed POS/fiscalisation software remains responsible for issuing the original compliant fiscal invoice.

## Original files and retention

Each file has a SHA-256, creation time, provenance and a retain-until date at least six calendar years after storage. Originals are capped at 1 MB each, eight per invoice and 4 MB total. API bodies remain capped at 2 MB. Downloads are tenant scoped, no-store, attachment-only and audited. Evidence is sealed after the buyer decision. No file deletion API is provided.

File bytes are held in the application database for this MVP. Use persistent PostgreSQL, restricted operator access, encrypted storage and tested encrypted backups for business records. A retain-until column does not provide backups or regulatory certification. The free Render demonstration uses temporary SQLite and resets; it is not a records archive.

Odoo 18 receives native `ir.attachment` records linked to the draft `account.move`, with acknowledgement verified against Odoo's native attachment checksum. Zoho Books receives one PDF evidence packet because its documented bill-attachment API exposes one attachment retrieval endpoint. That PDF embeds the exact originals, canonical JSON and a SHA-256 manifest. CBIN also retains the original independent files. Zoho's returned attachment bytes must match before delivery is marked stored; a different existing attachment is never overwritten. Packet attachments require a PDF viewer that supports embedded files. Provider sanitisation or attachment rewriting results in reconciliation, not false success.

## Odoo seller setup

Upgrade `addons/cbin_odoo` in a disposable Odoo 18 database. Configure company/partner CBIN IDs, TINs and separate VAT numbers. `cbin_export_payload` exports display names and addresses; `cbin_export_attachment` returns the stored invoice PDF or the accounting report PDF. The seller source-send endpoint now captures that PDF within the CBIN transaction. The add-on's outbound queue sends atomic invoice packages for new rows; pending legacy data-only rows remain deliverable.

Populate `cbin_fiscal_metadata` only from the actual fiscalisation module/provider. CBIN does not fabricate device signatures, fiscal-day numbers or fiscal references. The add-on has syntax checks and transport contract tests here; installation, permissions, fiscal report rendering and cron behaviour still require a real Odoo sandbox test.

## Paper receipt intake

The buyer's **Paper invoices** control accepts PDF/PNG/JPEG, sends it to an operator-reviewed extraction service and stores an immutable candidate and scan. It requires a matching verified seller and buyer and consistent invoice totals. Nothing enters the document inbox until the buyer reviews the original and checks the confirmation box. The resulting document is explicitly labelled `buyer_scan`; it is not described as an authenticated seller submission. Approval/bookkeeping remain separate. Duplicate scans of the same canonical transaction do not create another bill.

Wrong extraction results must remain unconfirmed and be corrected by the extraction provider. General automatic parsing of every Epson receipt layout is not claimed. No OCR provider is configured in the demonstration.

Extraction service contract (`CBIN_OCR_CONFIG` scoped by environment):

```json
{"test":{"reviewed":true,"provider":"YOUR_PROVIDER","evidence_reference":"YOUR_REVIEW_REFERENCE","url":"https://your-approved-service.example/extract","token_env":"CBIN_OCR_PROVIDER_TOKEN"}}
```

Set `CBIN_OCR_ALLOWLIST` to the exact URL. Requests are authenticated JSON with `file` (AttachmentUpload), `buyer_id`, and `environment`. The response must be `{ "invoice": <canonical Invoice schema> }`. The service is responsible for its OCR engine and reviewed layout mappings. No endpoint is inferred from an Epson printer or from Fiscal Harmony's software catalogue. Get permission and agree data handling with the provider before sending business documents.

## Fiscal verification

Buyer reviewers and operators can request a fiscal check when a receipt reference exists. Until a reviewed provider is configured, the API returns `FISCAL_VERIFIER_NOT_CONFIGURED`; it never returns simulated `valid` results. The portal also exposes the supplied official FDMS verification link.

`CBIN_FISCAL_VERIFIER_CONFIG` uses the same reviewed configuration shape, scoped by environment, with `token_env` set to `CBIN_FISCAL_PROVIDER_TOKEN`. Set `CBIN_FISCAL_VERIFIER_ALLOWLIST` to the exact HTTPS service endpoint. This is a CBIN bridge contract, **not a claim that ZIMRA or Fiscal Harmony exposes this endpoint**. A provider bridge must perform the actual documented authorised lookup and compare the original invoice data.

CBIN sends `document_id`, `environment`, the canonical `invoice` and `expected`:

```json
{"invoice_hash":"SHA256_OF_CANONICAL_INVOICE","external_reference":"SOURCE_REFERENCE","seller_tin":"SUPPLIER_TIN","buyer_tin":"BUYER_TIN","currency":"USD","grand_total_minor":23000,"receipt_reference":"ACTUAL_FISCAL_REFERENCE"}
```

The response must contain `status` (`valid`, `invalid`, `unavailable`), a safe `evidence_reference`, and `matched_invoice` equal to every expected field. The provider must verify the evidence; merely echoing CBIN's fields is not verification. Results are bound to the immutable invoice hash and audited. Set `CBIN_REQUIRE_FISCAL_VERIFICATION=true` to block buyer approval until a confirmed valid result exists. This setting does not decide input-VAT eligibility or replace accountant review. Current tax rates come from the supplier invoice and the buyer's configured tax catalogue, not sample values in templates.

## Upgrade existing business instances

Back up the database and stop API/worker processes for the upgrade. Install dependencies, build the TypeScript frontend and run:

```sh
cbin migrate-invoice-records
cbin archive-existing-invoices --limit 100
```

Repeat archive batches until zero rows are reported. Backfill creates copies of the already-stored payload, not invented supplier originals. Start the new API and worker. Existing v1.0 hashes without newly added optional party/fiscal fields remain stable. The schema migration only adds tables; it does not remove existing records.

## Two-machine acceptance test

1. Run the persistent pilot described in TWO-MACHINE-PILOT.md and migrate the database.
2. Seller machine: connect its actual Odoo sandbox and Epson printer; select a posted customer invoice addressed to the registered buyer and send it.
3. Buyer machine: connect a separate buyer credential/ERP database, open the received invoice, download its exact original, choose accounting records, preview and approve.
4. Run the worker. Confirm one draft bill and the expected attachments in the buyer ERP, not just the CBIN status. Check totals, currency, invoice references, supplier, every line account/tax and embedded originals.
5. Send the same source again, restart workers and repeat lookups. Confirm there is still one bill.
6. Disconnect the printer: no fiscal or physical-print success should be claimed. Disconnect the ERP: evidence remains in CBIN and attachment/posting state indicates the failure. Simulate lost acknowledgement: reconcile before another write.
7. Test a wrong buyer, invalid fiscal result, different existing ERP attachment and mismatched totals. These must not silently succeed.
8. With reviewed OCR/fiscal providers connected, test real receipt photos and invalid/valid FDMS samples. Check provenance and approval gates. These are provider/hardware acceptance tests, not completed by mock transport tests.

All 34 observed catalogue IDs retain the common structured import protocol. Native vendor support is still the tested code paths for Odoo 18 and Zoho Books; other products need reviewed export mappings or their own documented adapters. A catalogue listing is not proof of native integration. Live ledger writes remain gated in the connector registry until independently validated.
