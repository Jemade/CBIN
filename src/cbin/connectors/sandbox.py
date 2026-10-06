from cbin.connectors.base import ConnectorError


class SandboxAdapter:
    """Explicit test-only simulation. Never connects to or modifies a real ledger."""

    def capabilities(self):
        return {"draft_bill": True, "simulation": True}

    def recover_from_timeout(self, document_id, *context):
        return f"sandbox:{document_id}"

    def post_to_ledger(self, document_id, invoice, mapping):
        if invoice["currency"] not in {"USD", "ZWG"}:
            raise ConnectorError("SANDBOX_CURRENCY_UNSUPPORTED")
        return f"sandbox:{document_id}"

    def attach_document(self, bill_reference, document_id, file, create_allowed=True):
        return f"sandbox-file:{file.sha256}"

    def accounting_references(self):
        return {
            "source": "sandbox_simulation",
            "supported_documents": ["B2B_INVOICE", "CREDIT_NOTE"],
            "currency_exponents": {"USD": 2, "ZWG": 2},
            "suppliers": [
                {"id": "SUP-01", "name": "Demo supplier"},
                {"id": "SUP-02", "name": "Demo logistics supplier"},
            ],
            "accounts": [
                {"id": "EXP-OFFICE", "name": "Office supplies", "type": "expense"},
                {"id": "INV-GOODS", "name": "Goods inventory", "type": "inventory"},
                {"id": "ASSET-EQUIPMENT", "name": "Equipment", "type": "fixed_asset"},
            ],
            "items": [
                {"id": "ITEM-CABLE", "name": "Network cable", "code": "CABLE-001"},
                {"id": "ITEM-OFFICE", "name": "Office supplies", "code": "OFFICE-001"},
            ],
            "taxes": [
                {
                    "id": "TAX-15",
                    "name": "Demo tax 15%",
                    "rate": "15",
                    "treatment": "recoverable",
                    "account_id": "TAX-INPUT",
                    "account_name": "Demo input tax",
                },
                {"id": "TAX-0", "name": "Demo zero tax", "rate": "0", "treatment": "cost"},
            ],
            "payable_account": {"id": "AP", "name": "Accounts payable"},
            "purchase_orders": [
                {
                    "id": "PO-001",
                    "name": "PO-001 · Demo network cables",
                    "supplier_id": "SUP-01",
                    "currency": "USD",
                    "total_minor": 23000,
                }
            ],
        }
