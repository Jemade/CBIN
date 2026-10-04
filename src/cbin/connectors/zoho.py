import os
from decimal import Decimal

import httpx

from cbin.connectors.base import AmbiguousOutcome, ConnectorError


class ZohoBooksAdapter:
    """Sandbox-ready draft bill adapter. OAuth provisioning is an operator responsibility."""

    def __init__(self, config, client=None):
        self.config = config
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        allowed = {
            "https://www.zohoapis.com",
            "https://www.zohoapis.eu",
            "https://www.zohoapis.in",
            "https://www.zohoapis.com.au",
            "https://www.zohoapis.jp",
            "https://www.zohoapis.ca",
        }
        if config.get("base_url", "https://www.zohoapis.com") not in allowed:
            raise ConnectorError("ZOHO_REGION_UNSUPPORTED")

    def close(self):
        if self.owns_client:
            self.client.close()

    def capabilities(self):
        return {"draft_bill": True, "credit_note": False, "recovery": True}

    def request(self, method, path, **kwargs):
        token = os.environ.get(self.config["token_env"])
        if not token:
            raise ConnectorError("ZOHO_TOKEN_MISSING")
        try:
            response = self.client.request(
                method,
                self.config.get("base_url", "https://www.zohoapis.com") + "/books/v3" + path,
                headers={"Authorization": f"Zoho-oauthtoken {token}"},
                params={
                    "organization_id": self.config["organization_id"],
                    **kwargs.pop("params", {}),
                },
                **kwargs,
            )
        except httpx.TransportError:
            raise AmbiguousOutcome("ZOHO_TRANSPORT_UNCERTAIN") from None
        if response.status_code >= 500:
            raise AmbiguousOutcome("ZOHO_SERVER_UNCERTAIN")
        if response.status_code >= 400:
            raise ConnectorError(f"ZOHO_HTTP_{response.status_code}")
        try:
            result = response.json()
            if result.get("code") != 0:
                raise ConnectorError("ZOHO_API_REJECTED")
            return result
        except ValueError:
            raise AmbiguousOutcome("ZOHO_RESPONSE_UNCERTAIN") from None

    def list_reference_pages(self, path, key, **params):
        rows = []
        for page in range(1, 101):
            data = self.request("GET", path, params={"page": page, "per_page": 200, **params})
            rows.extend(data.get(key, []))
            if not data.get("page_context", {}).get("has_more_page", False):
                return rows
        raise ConnectorError("ZOHO_REFERENCE_PAGE_LIMIT")

    def accounting_references(self):
        contacts = self.list_reference_pages("/contacts", "contacts", contact_type="vendor")
        accounts = self.list_reference_pages("/chartofaccounts", "chartofaccounts")
        items = self.list_reference_pages("/items", "items")
        taxes = self.list_reference_pages("/settings/taxes", "taxes")
        account_types = {
            "expense",
            "other_expense",
            "cost_of_goods_sold",
            "stock",
            "inventory",
            "fixed_asset",
            "other_current_asset",
            "other_asset",
        }
        catalogue = {
            "source": "zoho_books_test_adapter",
            "supported_documents": ["B2B_INVOICE"],
            "currency_exponents": self.config.get("currency_exponents", {}),
            "suppliers": [
                {"id": str(r["contact_id"]), "name": r["contact_name"]}
                for r in contacts
                if r.get("status") == "active" and r.get("contact_type", "vendor") == "vendor"
            ],
            "accounts": [
                {
                    "id": str(r["account_id"]),
                    "name": r["account_name"],
                    "code": r.get("account_code", ""),
                    "type": r["account_type"],
                }
                for r in accounts
                if r.get("is_active") and r.get("account_type") in account_types
            ],
            "items": [
                {"id": str(r["item_id"]), "name": r["name"], "code": r.get("sku", "")}
                for r in items
                if r.get("status") == "active"
            ],
            "taxes": [
                {
                    "id": str(r["tax_id"]),
                    "name": r["tax_name"],
                    "rate": str(r["tax_percentage"]),
                    "treatment": "erp_managed",
                }
                for r in taxes
                if r.get("tax_factor", "rate") == "rate" and r.get("is_active", True)
            ],
            "purchase_orders": [],
        }
        payable = next(
            (
                r
                for r in accounts
                if r.get("is_active") and r.get("account_type") == "accounts_payable"
            ),
            None,
        )
        if payable:
            catalogue["payable_account"] = {
                "id": str(payable["account_id"]),
                "name": payable["account_name"],
            }
        if self.config.get("purchase_order_lookup", False):
            for r in self.list_reference_pages("/purchaseorders", "purchaseorders"):
                exponent = catalogue["currency_exponents"].get(r.get("currency_code"))
                if exponent is not None and r.get("status") in {
                    "open",
                    "issued",
                    "partially_billed",
                }:
                    minor = Decimal(str(r["total"])) * Decimal(10) ** exponent
                    if minor != minor.to_integral_value():
                        raise ConnectorError("ZOHO_PURCHASE_ORDER_PRECISION")
                    catalogue["purchase_orders"].append(
                        {
                            "id": str(r["purchaseorder_id"]),
                            "name": r["purchaseorder_number"],
                            "supplier_id": str(r["vendor_id"]),
                            "currency": r["currency_code"],
                            "total_minor": int(minor),
                        }
                    )
        return catalogue

    def recover_from_timeout(self, document_id, invoice=None, mapping=None):
        reference = f"CBIN-{document_id}"
        result = self.request("GET", "/bills", params={"reference_number": reference})
        matches = [
            bill for bill in result.get("bills", []) if bill.get("reference_number") == reference
        ]
        if len(matches) > 1:
            raise AmbiguousOutcome("ZOHO_MULTIPLE_MATCHES")
        if not matches:
            return None
        if invoice is None or mapping is None:
            raise AmbiguousOutcome("ZOHO_RECOVERY_CONTEXT_MISSING")
        bill_id = str(matches[0]["bill_id"])
        bill = self.request("GET", f"/bills/{bill_id}").get("bill", {})
        exponent = self.config.get("currency_exponents", {}).get(invoice["currency"])
        if exponent is None:
            raise ConnectorError("ZOHO_CURRENCY_EXPONENT_MISSING")
        if (
            bill.get("status") != "draft"
            or str(bill.get("vendor_id")) != mapping["supplier_reference"]
            or str(bill.get("currency_id"))
            != self.config.get("currency_ids", {}).get(invoice["currency"])
            or Decimal(str(bill.get("total", "-1"))) * Decimal(10) ** exponent
            != invoice["totals"]["grand_total_minor"]
        ):
            raise AmbiguousOutcome("ZOHO_RECOVERED_BILL_MISMATCH")
        return bill_id

    def post_to_ledger(self, document_id, invoice, mapping):
        if invoice["document_type"] != "B2B_INVOICE":
            raise ConnectorError("ZOHO_CREDIT_NOTE_NOT_IMPLEMENTED")
        currency_id = self.config.get("currency_ids", {}).get(invoice["currency"])
        if not currency_id:
            raise ConnectorError("ZOHO_CURRENCY_MAPPING_MISSING")
        # Convert minor units at the vendor boundary only. Canonical values remain integers.
        exponent = self.config.get("currency_exponents", {}).get(invoice["currency"])
        if exponent is None:
            raise ConnectorError("ZOHO_CURRENCY_EXPONENT_MISSING")
        divisor = Decimal(10) ** exponent
        lines = []
        allocations = {
            r["line_index"]: r["account_reference"] for r in mapping.get("line_allocations", [])
        }
        for index, line in enumerate(invoice["line_items"]):
            tax_id = mapping["tax_mapping"].get(format(Decimal(line["tax_rate"]).normalize(), "f"))
            if tax_id is None:
                raise ConnectorError("ZOHO_TAX_MAPPING_MISSING")
            lines.append(
                {
                    "item_id": mapping["sku_mapping"][line["item_code"]],
                    "account_id": allocations.get(index, mapping["account_reference"]),
                    "description": line["description"],
                    "tax_id": tax_id,
                    "quantity": float(Decimal(line["quantity"])),
                    "rate": float(Decimal(line["unit_price_minor"]) / divisor),
                }
            )
        result = self.request(
            "POST",
            "/bills",
            json={
                "vendor_id": mapping["supplier_reference"],
                "currency_id": currency_id,
                "bill_number": f"CBIN-{document_id}",
                "reference_number": f"CBIN-{document_id}",
                "date": invoice["issued_at"],
                "notes": "Supplier invoice: "
                + invoice["external_reference"]
                + (
                    "; buyer PO header check: " + mapping["purchase_order_reference"]
                    if mapping.get("purchase_order_reference")
                    else ""
                ),
                "is_inclusive_tax": False,
                "line_items": lines,
            },
        )
        bill = result.get("bill", {})
        if not bill.get("bill_id"):
            raise AmbiguousOutcome("ZOHO_BILL_ID_MISSING")
        if bill.get("status") != "draft":
            raise AmbiguousOutcome("ZOHO_DRAFT_STATUS_UNCONFIRMED")
        # The sandbox pilot must verify provider rounding, tax and FX against canonical totals.
        total = Decimal(str(bill.get("total", "-1"))) * divisor
        if total != invoice["totals"]["grand_total_minor"]:
            raise AmbiguousOutcome("ZOHO_TOTAL_MISMATCH")
        return str(bill["bill_id"])
