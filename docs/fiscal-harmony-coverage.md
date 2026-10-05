# Fiscal Harmony software coverage

Checked 4 October 2026. Source: https://fiscalharmony.co.zw/products/fiscalisation/integrations/

The public integration page advertises 30+ applications. Its embedded catalogue currently contains **34 entries**. This includes accounting software, ERP, POS, a spreadsheet, e-commerce and a database, plus separately listed product editions. It is not a list of 34 distinct accounting vendors. Names below preserve the observed catalogue; a listing does not establish CBIN read/write access, contractual permission or a tested integration.

All 34 are registered in the CBIN software catalogue and can use the shared reviewed structured-import route. Each installation still needs its own confirmed field mapping and authorized source extraction. Only Odoo 18 seller capture/buyer posting and Zoho Books buyer posting have repository prototypes; there are no live-verified integrations.

| ID | Observed product | Category | CBIN native status |
| --- | --- | --- | --- |
| `hpos` | HPOS | POS | Native connector pending |
| `xero` | Xero | Accounting | Native connector pending |
| `quickbooksdesktop` | QuickBooks Desktop | Accounting | Native connector pending |
| `quickbooksonline` | QuickBooks Online | Accounting | Native connector pending |
| `excel` | Microsoft Excel | Other | Native connector pending |
| `pastelpartner` | Sage Pastel Partner | Accounting | Native connector pending |
| `pastelxpress` | Sage Pastel Xpress | Accounting | Native connector pending |
| `evolution` | Sage Evolution | ERP | Native connector pending |
| `sageone` | Sage Business Cloud Accounting (Sage One) | Accounting | Native connector pending |
| `sagepastelpos` | Sage Pastel POS | POS | Native connector pending |
| `sageevolutionpos` | Sage Evolution POS | POS | Native connector pending |
| `zohobilling` | Zoho Billing | Accounting | Native connector pending |
| `zohobooks` | Zoho Books | Accounting | Test posting prototype |
| `zohoinventory` | Zoho Inventory | ERP | Native connector pending |
| `ivend` | iVend Retail | ERP | Native connector pending |
| `cin7core` | Cin7 Core (formerly DEAR Systems) | ERP | Native connector pending |
| `sap` | SAP Business One | ERP | Native connector pending |
| `palladium` | Palladium Business Solutions | ERP | Native connector pending |
| `palladiumpos` | Palladium POS | POS | Native connector pending |
| `matrix` | Matrix Software | ERP | Native connector pending |
| `meatmatrixpos` | Meat Matrix POS | POS | Native connector pending |
| `manager` | Manager.io | Accounting | Native connector pending |
| `erpnext` | ERPNext | ERP | Native connector pending |
| `shopify` | Shopify | Ecom | Native connector pending |
| `fincon` | Fincon | ERP Accounting | Native connector pending |
| `4pos` | 4POS | POS | Native connector pending |
| `intellectpos` | Sales Intellect POS | POS | Native connector pending |
| `gaap` | GAAP Unity | POS | Native connector pending |
| `odoo17` | Odoo v17 | ERP | Native connector pending |
| `odoo18` | Odoo v18 | ERP | Test capture/posting prototype |
| `odoo19` | Odoo v19 | ERP | Native connector pending |
| `odoocloud` | Odoo Cloud | ERP | Native connector pending |
| `salesplay` | Sales Play | POS | Native connector pending |
| `oracledb` | Oracle DB | Other | Native connector pending |

## Implementation approach

Cloud and self-hosted products: investigate documented APIs, signed events, consent and supported editions. Desktop products: investigate an authorized SDK or structured export through a durable local agent. Excel: accept a reviewed structured export, not arbitrary workbook code. Oracle DB: investigate an approved read-only extraction method; a database listing is not an accounting contract. These are CBIN engineering proposals, not claims about vendor API capabilities.

Catalogue listing, capture, normalization, buyer posting and recovery are separate capabilities. Test each direction independently. A POS or e-commerce application may only be a seller source; do not promise purchase-ledger posting into every listed product.

Fiscal Harmony remains responsible for its fiscalisation path. CBIN must never submit a second tax transaction just to move a document to a buyer. No Fiscal Harmony partnership, shared account access, reusable proprietary plugin or upstream webhook subscription is implied. Use client authorization and documented APIs to receive original structured data and supplied fiscal evidence.

## Shared mapped import

`GET /v1/connectors` returns the observed list with source/date, capture/posting status and live-verification flags. The portal provides search and visible status.

`POST /v1/connectors/{software_id}/documents` accepts `profile_id` and `vendor_payload`, with the same bearer credential and Idempotency-Key requirements as canonical ingestion. Source payloads are normalized, validated and routed through the same durable workflow. Canonical fields and mappings cannot bypass seller identity, amount checks, tenant isolation or cross-connector duplicate detection. Raw vendor payloads are not stored.

Operators configure `CBIN_IMPORT_PROFILES` by environment → business ID → software ID → profile ID. Each profile needs `reviewed: true` and an evidence reference. Tenant users cannot supply their own mapping rules in requests. Configuration must describe a real authorized deployment; the example below is a fictional export schema, not a vendor-native schema.

```json
{
  "test": {
    "CBIN-DEMO-SELLER": {
      "xero": {
        "pilot-export-v1": {
          "reviewed": true,
          "evidence_reference": "YOUR_REVIEWED_SAMPLE_REFERENCE",
          "fields": {
            "external_reference": {
              "path": "invoice.number"
            },
            "issued_at": {
              "path": "invoice.date"
            },
            "currency": {
              "path": "invoice.currency"
            },
            "document_type": {
              "constant": "B2B_INVOICE"
            },
            "seller": {
              "path": "seller"
            },
            "buyer": {
              "path": "buyer"
            },
            "totals": {
              "path": "totals"
            }
          },
          "line_items_path": "invoice.lines",
          "line_fields": {
            "item_code": {
              "path": "sku"
            },
            "description": {
              "path": "description"
            },
            "quantity": {
              "path": "quantity",
              "transform": "decimal_string"
            },
            "unit_price_minor": {
              "path": "unit_price",
              "transform": "major_to_minor",
              "minor_exponent": 2
            },
            "tax_rate": {
              "path": "tax_rate",
              "transform": "decimal_string"
            }
          }
        }
      }
    }
  }
}
```

Mapping paths read dictionary keys separated by dots. Transforms are declarative; no evaluation, SQL or executable scripts are accepted. Decimal conversion requires strings or integers, rejects floats/nonfinite values and rejects fractional minor-unit precision loss. Unknown or missing fields and mismatched totals fail instead of receiving invented defaults. Protect profile configuration in an operator-managed environment file; multi-tenant configuration management belongs in a future operator service.

## Required proof per product/edition

1. Exact edition/version/hosting and written customer authorization.
2. Documented capture method and redacted source fixtures for invoices and corrections.
3. Buyer ID/TIN, stable source references, lines, tax treatment, currency and FX snapshot.
4. Reviewed mapping and round-trip amount reconciliation.
5. Offline, duplicate, authentication-expiry and timeout recovery tests.
6. Buyer-side draft API and SKU/tax/account mapping where that product has a ledger.
7. Real sandbox evidence before advertising a working integration.

## Supporting references

- https://fiscalharmony.co.zw/downloads/ documents desktop plugin targets; installation requirements do not grant CBIN access.
- https://support.fiscalharmony.co.zw/portal/en/kb/articles/get-started explains product-specific setup and fiscalisation outcomes.
- https://support.fiscalharmony.co.zw/portal/en/kb/articles/tax-mapping-in-fiscal-harmony-checks-and-integration-guides distinguishes tax setup by integration.

The catalogue includes an observed source hash for change tracking. Descriptions, branding assets and proprietary plugin code were not copied into CBIN. Additional names mentioned elsewhere, such as Wingate, are not silently substituted for entries in this particular 34-item snapshot.

## Automated coverage evidence

Every catalogue ID is parametrized through reviewed mapping, submission, durable routing, buyer approval and Sandbox posting in `tests/test_odoo.py`. The inputs are canonical test fixtures under an explicit protocol-test mapping. This proves the common CBIN mechanism for all 34 IDs, not extraction from those 34 products or native posting into them. No row is live-verified. Exact vendor samples, credentials, supported editions and sandbox acceptance evidence are still required for each row above.
