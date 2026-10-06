"""Odoo 18 test adapter: read master data, capture invoices, create draft vendor bills."""

import base64
import hashlib
import json
import os
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from cbin.connectors.base import AmbiguousOutcome, ConnectorError
from cbin.schemas import AttachmentUpload, Invoice


class OdooAdapter:
    def __init__(self, config, client=None):
        self.config = config
        self.base_url = config.get("base_url", "").rstrip("/")
        parsed = urlsplit(self.base_url)
        allowed = json.loads(os.getenv("CBIN_ERP_ALLOWLIST", "[]"))
        if (
            self.base_url not in allowed
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ConnectorError("ODOO_ENDPOINT_NOT_APPROVED")
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        self.uid = None

    def close(self):
        if self.owns_client:
            self.client.close()

    def capabilities(self):
        return {"draft_bill": True, "capture": True, "credit_note": False, "recovery": True}

    def rpc(self, service, method, args, write=False):
        try:
            response = self.client.post(
                self.base_url + "/jsonrpc",
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {"service": service, "method": method, "args": args},
                    "id": str(uuid4()),
                },
            )
        except httpx.TransportError:
            raise (AmbiguousOutcome if write else ConnectorError)(
                "ODOO_TRANSPORT_UNCERTAIN" if write else "ODOO_READ_UNAVAILABLE"
            ) from None
        if response.status_code >= 500:
            raise (AmbiguousOutcome if write else ConnectorError)("ODOO_SERVER_UNCERTAIN")
        if response.status_code >= 400:
            raise ConnectorError(f"ODOO_HTTP_{response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise (AmbiguousOutcome if write else ConnectorError)("ODOO_RESPONSE_INVALID") from None
        if "error" in data:
            raise (AmbiguousOutcome if write else ConnectorError)("ODOO_RPC_REJECTED")
        if "result" not in data:
            raise (AmbiguousOutcome if write else ConnectorError)("ODOO_RESULT_MISSING")
        return data["result"]

    def execute(self, model, method, args, kwargs=None, write=False):
        password = os.environ.get(self.config["password_env"])
        if not password:
            raise ConnectorError("ODOO_PASSWORD_MISSING")
        if self.uid is None:
            self.uid = self.rpc(
                "common",
                "authenticate",
                [self.config["database"], self.config["username"], password, {}],
            )
            if not self.uid:
                raise ConnectorError("ODOO_AUTH_FAILED")
        kwargs = {
            **(kwargs or {}),
            "context": {
                "allowed_company_ids": [self.config["company_id"]],
                "company_id": self.config["company_id"],
            },
        }
        return self.rpc(
            "object",
            "execute_kw",
            [self.config["database"], self.uid, password, model, method, args, kwargs],
            write=write,
        )

    def records(self, model, domain, fields, cap=10000):
        rows = []
        while len(rows) < cap:
            batch = self.execute(
                model,
                "search_read",
                [domain],
                {"fields": fields, "limit": 200, "offset": len(rows), "order": "id"},
            )
            rows.extend(batch)
            if len(batch) < 200:
                return rows
        raise ConnectorError("ODOO_REFERENCE_PAGE_LIMIT")

    def accounting_references(self):
        company = self.config["company_id"]
        suppliers = self.records(
            "res.partner",
            [
                ["supplier_rank", ">", 0],
                ["active", "=", True],
                "|",
                ["company_id", "=", False],
                ["company_id", "=", company],
            ],
            ["name", "ref"],
        )
        accounts = self.records(
            "account.account",
            [["company_ids", "in", [company]], ["deprecated", "=", False]],
            ["name", "code", "account_type"],
        )
        items = self.records(
            "product.product",
            [
                ["purchase_ok", "=", True],
                ["active", "=", True],
                "|",
                ["company_id", "=", False],
                ["company_id", "=", company],
            ],
            ["name", "default_code"],
        )
        taxes = self.records(
            "account.tax",
            [
                ["company_id", "=", company],
                ["type_tax_use", "=", "purchase"],
                ["amount_type", "=", "percent"],
                ["price_include", "=", False],
                ["active", "=", True],
            ],
            ["name", "amount"],
        )
        allowed_types = {
            "expense",
            "expense_direct_cost",
            "expense_depreciation",
            "asset_current",
            "asset_non_current",
            "asset_fixed",
            "asset_prepayments",
        }
        return {
            "source": "odoo18_test_adapter",
            "supported_documents": ["B2B_INVOICE"],
            "currency_exponents": self.config.get("currency_exponents", {}),
            "suppliers": [
                {"id": str(r["id"]), "name": r["name"], "code": r["ref"] or ""} for r in suppliers
            ],
            "accounts": [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "code": r["code"],
                    "type": r["account_type"],
                }
                for r in accounts
                if r["account_type"] in allowed_types
            ],
            "items": [
                {"id": str(r["id"]), "name": r["name"], "code": r["default_code"] or ""}
                for r in items
            ],
            "taxes": [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "rate": str(r["amount"]),
                    "treatment": "erp_managed",
                }
                for r in taxes
            ],
            "purchase_orders": [],
        }

    def source_documents(self):
        rows = self.execute(
            "account.move",
            "search_read",
            [
                [
                    ["company_id", "=", self.config["company_id"]],
                    ["move_type", "=", "out_invoice"],
                    ["state", "=", "posted"],
                ]
            ],
            {
                "fields": ["name", "invoice_date", "amount_total", "currency_id"],
                "limit": 50,
                "order": "id desc",
            },
        )
        return [
            {
                "source_id": str(r["id"]),
                "reference": r["name"],
                "issued_at": r["invoice_date"],
                "amount": str(r["amount_total"]),
                "currency": r["currency_id"][1],
            }
            for r in rows
        ]

    def source_invoice(self, ident):
        result = self.execute("account.move", "cbin_export_payload", [[int(ident)]])
        try:
            return Invoice.model_validate(result)
        except ValidationError:
            raise ConnectorError("ODOO_SOURCE_PAYLOAD_INVALID") from None

    def source_attachment(self, ident):
        result = self.execute("account.move", "cbin_export_attachment", [[int(ident)]])
        try:
            return AttachmentUpload.model_validate(result)
        except ValidationError:
            raise ConnectorError("ODOO_SOURCE_ATTACHMENT_INVALID") from None

    def attach_document(self, bill_reference, document_id, file, create_allowed=True):
        name = f"CBIN-{document_id}-{file.sha256}.{file.filename.rsplit('.', 1)[-1]}"
        expected = hashlib.sha1(file.content).hexdigest()  # Odoo's native attachment checksum.
        domain = [
            ["res_model", "=", "account.move"],
            ["res_id", "=", int(bill_reference)],
            ["name", "=", name],
        ]

        def lookup():
            return self.execute(
                "ir.attachment", "search_read", [domain], {"fields": ["checksum"], "limit": 2}
            )

        existing = lookup()
        if existing:
            if len(existing) != 1 or existing[0].get("checksum") != expected:
                raise AmbiguousOutcome("ODOO_ATTACHMENT_MISMATCH")
            return str(existing[0]["id"])
        if not create_allowed:
            raise AmbiguousOutcome("ODOO_ATTACHMENT_REQUIRES_RECONCILIATION")
        ident = self.execute(
            "ir.attachment",
            "create",
            [
                {
                    "name": name,
                    "type": "binary",
                    "res_model": "account.move",
                    "res_id": int(bill_reference),
                    "mimetype": file.media_type,
                    "datas": base64.b64encode(file.content).decode("ascii"),
                }
            ],
            write=True,
        )
        rows = lookup()
        if (
            len(rows) != 1
            or rows[0].get("checksum") != expected
            or str(rows[0]["id"]) != str(ident)
        ):
            raise AmbiguousOutcome("ODOO_ATTACHMENT_UNCONFIRMED")
        return str(ident)

    def recover_from_timeout(self, document_id, invoice=None, mapping=None):
        rows = self.execute(
            "account.move",
            "search_read",
            [
                [
                    ["company_id", "=", self.config["company_id"]],
                    ["move_type", "=", "in_invoice"],
                    ["ref", "=", f"CBIN-{document_id}"],
                ]
            ],
            {
                "fields": [
                    "state",
                    "partner_id",
                    "currency_id",
                    "amount_total",
                    "invoice_line_ids",
                ],
                "limit": 2,
            },
        )
        if not rows:
            return None
        if len(rows) != 1 or not invoice or not mapping:
            raise AmbiguousOutcome("ODOO_RECOVERY_CONTEXT_OR_DUPLICATE")
        row = rows[0]
        exponent = self.config.get("currency_exponents", {}).get(invoice["currency"])
        if (
            exponent is None
            or row["state"] != "draft"
            or str(row["partner_id"][0]) != mapping["supplier_reference"]
            or row["currency_id"][0] != self.config.get("currency_ids", {}).get(invoice["currency"])
            or Decimal(str(row["amount_total"])) * Decimal(10) ** exponent
            != invoice["totals"]["grand_total_minor"]
        ):
            raise AmbiguousOutcome("ODOO_RECOVERED_BILL_MISMATCH")
        lines = self.records(
            "account.move.line",
            [["move_id", "=", row["id"]], ["display_type", "=", "product"]],
            ["product_id", "account_id", "tax_ids", "quantity", "price_unit", "discount"],
        )
        allocations = {
            r["line_index"]: r["account_reference"] for r in mapping.get("line_allocations", [])
        }
        if len(lines) != len(invoice["line_items"]):
            raise AmbiguousOutcome("ODOO_RECOVERED_LINES_MISMATCH")
        for index, (actual, expected) in enumerate(zip(lines, invoice["line_items"])):
            rate = format(Decimal(expected["tax_rate"]).normalize(), "f")
            if (
                not actual["product_id"]
                or not actual["account_id"]
                or str(actual["product_id"][0]) != mapping["sku_mapping"][expected["item_code"]]
                or str(actual["account_id"][0])
                != allocations.get(index, mapping["account_reference"])
                or sorted(map(str, actual["tax_ids"])) != [mapping["tax_mapping"][rate]]
                or Decimal(str(actual["quantity"])) != Decimal(expected["quantity"])
                or Decimal(str(actual["price_unit"])) * Decimal(10) ** exponent
                != expected["unit_price_minor"]
                or Decimal(str(actual["discount"])) != 0
            ):
                raise AmbiguousOutcome("ODOO_RECOVERED_LINES_MISMATCH")
        return str(row["id"])

    def post_to_ledger(self, document_id, invoice, mapping):
        if invoice["document_type"] != "B2B_INVOICE":
            raise ConnectorError("ODOO_CREDIT_NOTE_NOT_IMPLEMENTED")
        exponent = self.config.get("currency_exponents", {}).get(invoice["currency"])
        currency = self.config.get("currency_ids", {}).get(invoice["currency"])
        if exponent is None or currency is None:
            raise ConnectorError("ODOO_CURRENCY_MAPPING_MISSING")
        allocations = {
            r["line_index"]: r["account_reference"] for r in mapping.get("line_allocations", [])
        }
        lines = []
        for i, line in enumerate(invoice["line_items"]):
            tax = mapping["tax_mapping"].get(format(Decimal(line["tax_rate"]).normalize(), "f"))
            if tax is None:
                raise ConnectorError("ODOO_TAX_MAPPING_MISSING")
            lines.append(
                [
                    0,
                    0,
                    {
                        "product_id": int(mapping["sku_mapping"][line["item_code"]]),
                        "account_id": int(allocations.get(i, mapping["account_reference"])),
                        "name": line["description"],
                        "quantity": float(Decimal(line["quantity"])),
                        "price_unit": float(
                            Decimal(line["unit_price_minor"]) / Decimal(10) ** exponent
                        ),
                        "tax_ids": [[6, 0, [int(tax)]]],
                    },
                ]
            )
        ident = self.execute(
            "account.move",
            "create",
            [
                {
                    "move_type": "in_invoice",
                    "company_id": self.config["company_id"],
                    "partner_id": int(mapping["supplier_reference"]),
                    "currency_id": currency,
                    "invoice_date": invoice["issued_at"],
                    "ref": f"CBIN-{document_id}",
                    "invoice_line_ids": lines,
                }
            ],
            write=True,
        )
        reference = self.recover_from_timeout(document_id, invoice, mapping)
        if str(ident) != reference:
            raise AmbiguousOutcome("ODOO_CREATED_BILL_UNCONFIRMED")
        return reference
