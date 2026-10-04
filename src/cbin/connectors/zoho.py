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
        for line in invoice["line_items"]:
            tax_id = mapping["tax_mapping"].get(str(Decimal(line["tax_rate"]).normalize()))
            if tax_id is None:
                raise ConnectorError("ZOHO_TAX_MAPPING_MISSING")
            lines.append(
                {
                    "item_id": mapping["sku_mapping"][line["item_code"]],
                    "account_id": mapping["account_reference"],
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
