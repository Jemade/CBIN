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
