"""Tenant-scoped accounting master data, suggestions and exact minor-unit previews."""

from decimal import Decimal

from cbin.connectors.base import ConnectorError
from cbin.connectors.registry import adapter_for
from cbin.db import AccountingSnapshot, BuyerMapping
from cbin.schemas import Invoice
from cbin.service import DomainError, digest, now, record


def scope(credential, seller_id=None):
    return digest([credential.environment, credential.business_id, seller_id])


def references(db, credential, refresh=False):
    key = scope(credential)
    row = db.get(AccountingSnapshot, key)
    # Reference IDs must belong to the current configured adapter, not an older connection.
    from cbin.connectors.registry import connection_fingerprint

    fingerprint = connection_fingerprint(credential.business_id, credential.environment)
    if (
        row is None
        or refresh
        or now() - row.refreshed_at > 900
        or row.catalogue.get("connection_fingerprint") != fingerprint
    ):
        adapter = None
        try:
            adapter = adapter_for(credential.business_id, credential.environment)
            if not hasattr(adapter, "accounting_references"):
                raise ConnectorError("ACCOUNTING_LOOKUP_NOT_IMPLEMENTED")
            catalogue = adapter.accounting_references()
            catalogue["connection_fingerprint"] = fingerprint
        except ConnectorError as exc:
            raise DomainError(
                "ACCOUNTING_UNAVAILABLE",
                f"Accounting reference lookup unavailable: {exc}",
                503,
                True,
            ) from None
        finally:
            if adapter and hasattr(adapter, "close"):
                adapter.close()
        row = AccountingSnapshot(
            scope=key,
            business_id=credential.business_id,
            environment=credential.environment,
            catalogue=catalogue,
            refreshed_at=now(),
        )
        db.merge(row)
        db.flush()
    # Fingerprint is internal metadata, never a credential or user-visible field.
    return {k: v for k, v in row.catalogue.items() if k != "connection_fingerprint"} | {
        "refreshed_at": row.refreshed_at
    }


def buyer_access(credential, document):
    if credential.role not in {"reviewer", "admin"} or document.buyer_id != credential.business_id:
        raise DomainError("FORBIDDEN", "Buyer reviewer or administrator required", 403)


def suggestions(db, credential, document, catalogue):
    row = db.get(BuyerMapping, scope(credential, document.seller_id))
    from cbin.connectors.registry import connection_fingerprint

    if row and row.mapping.get("connection_fingerprint") != connection_fingerprint(
        credential.business_id, credential.environment
    ):
        row = None
    saved = row.mapping if row else {}

    def available(kind, value):
        return value if any(r["id"] == value for r in catalogue[kind]) else ""

    # No guesses based on similar names: reuse explicit approved buyer choices only.
    return {
        "supplier_reference": available("suppliers", saved.get("supplier_reference")),
        "sku_mapping": {
            line["item_code"]: available(
                "items", saved.get("sku_mapping", {}).get(line["item_code"])
            )
            for line in document.payload["line_items"]
        },
        "tax_mapping": {
            rate_key(line["tax_rate"]): available(
                "taxes", saved.get("tax_mapping", {}).get(rate_key(line["tax_rate"]))
            )
            for line in document.payload["line_items"]
        },
        "line_allocations": [
            {
                "line_index": i,
                "account_reference": available(
                    "accounts",
                    saved.get("account_mapping", {}).get(line["item_code"]),
                ),
            }
            for i, line in enumerate(document.payload["line_items"])
        ],
        "saved": row is not None,
    }


def rate_key(rate):
    return format(Decimal(str(rate)).normalize(), "f")


def preview(db, credential, document, body):
    buyer_access(credential, document)
    catalogue = references(db, credential)
    if document.payload["document_type"] not in catalogue["supported_documents"]:
        raise DomainError(
            "DOCUMENT_TYPE_UNSUPPORTED", "Configured adapter cannot post this document type", 422
        )
    currency = document.payload["currency"]
    exponent = catalogue["currency_exponents"].get(currency)
    if exponent is None:
        raise DomainError(
            "CURRENCY_UNSUPPORTED", "Configure this currency in the accounting adapter", 422
        )

    def selected(kind, ident):
        found = next((r for r in catalogue[kind] if r["id"] == ident), None)
        if found is None:
            raise DomainError(
                "ACCOUNTING_SELECTION_INVALID", f"Select an available {kind} record", 422
            )
        return found

    supplier = selected("suppliers", body.supplier_reference)
    invoice = Invoice.model_validate(document.payload)
    if set(body.sku_mapping) != {line.item_code for line in invoice.line_items}:
        raise DomainError("MAPPING_INCOMPLETE", "Map every seller item", 422)
    allocations = {line.line_index: line.account_reference for line in body.line_allocations}
    if len(allocations) != len(body.line_allocations) or set(allocations) != set(
        range(len(invoice.line_items))
    ):
        raise DomainError(
            "ALLOCATION_INCOMPLETE", "Select an account for every invoice line exactly once", 422
        )
    if body.account_reference != allocations[0]:
        raise DomainError(
            "ACCOUNTING_SELECTION_INVALID",
            "Default account must match the first line allocation",
            422,
        )
    if set(body.tax_mapping) != {rate_key(line.tax_rate) for line in invoice.line_items}:
        raise DomainError("TAX_MAPPING_INCOMPLETE", "Map every invoice tax rate", 422)
    entries = {}

    def add(account, name, amount):
        key = (account, name)
        entries[key] = entries.get(key, 0) + amount

    mapped_lines = []
    for i, line in enumerate(invoice.line_items):
        item = selected("items", body.sku_mapping[line.item_code])
        account = selected("accounts", allocations[i])
        tax = selected("taxes", body.tax_mapping[rate_key(line.tax_rate)])
        if Decimal(str(tax["rate"])) != line.tax_rate:
            raise DomainError(
                "TAX_RATE_MISMATCH", "Selected tax code differs from the invoice rate", 422
            )
        add(account["id"], account["name"], line.subtotal)
        if tax.get("treatment") == "cost":
            add(account["id"], account["name"], line.tax)
        else:
            add(
                tax.get("account_id", "tax-control"),
                tax.get("account_name", "Tax control · ERP treatment"),
                line.tax,
            )
        mapped_lines.append(
            {
                "line_index": i,
                "item_id": item["id"],
                "quantity": str(line.quantity),
                "subtotal_minor": line.subtotal,
                "tax_minor": line.tax,
                "account_id": account["id"],
            }
        )
    po_result = None
    if body.purchase_order_reference:
        po = selected("purchase_orders", body.purchase_order_reference)
        if po["supplier_id"] != supplier["id"] or po["currency"] != currency:
            raise DomainError(
                "PURCHASE_ORDER_MISMATCH", "Purchase order supplier or currency differs", 422
            )
        if po["total_minor"] != invoice.totals.grand_total_minor:
            raise DomainError(
                "PURCHASE_ORDER_MISMATCH",
                "Invoice total differs from purchase order; review outside the matching flow",
                422,
            )
        po_result = {
            "id": po["id"],
            "name": po["name"],
            "status": "supplier_currency_total_match",
            "notice": "Header match only; quantities, receipts and prior billed balances still need review.",
        }
    credit = invoice.document_type == "CREDIT_NOTE"
    rows = [
        {
            "account_id": a,
            "account_name": n,
            "debit_minor": 0 if credit else v,
            "credit_minor": v if credit else 0,
        }
        for (a, n), v in entries.items()
        if v
    ]
    payable = catalogue.get(
        "payable_account", {"id": "supplier-payable", "name": "Supplier payable"}
    )
    rows.append(
        {
            "account_id": payable["id"],
            "account_name": f"{payable['name']} · {supplier['name']}",
            "debit_minor": invoice.totals.grand_total_minor if credit else 0,
            "credit_minor": 0 if credit else invoice.totals.grand_total_minor,
        }
    )
    debits = sum(r["debit_minor"] for r in rows)
    credits = sum(r["credit_minor"] for r in rows)
    if debits != credits:
        raise DomainError("PREVIEW_UNBALANCED", "Proposed accounting entry does not balance", 422)
    return {
        "currency": currency,
        "currency_exponent": exponent,
        "entries": rows,
        "debit_minor": debits,
        "credit_minor": credits,
        "balanced": True,
        "purchase_order": po_result,
        "line_allocations": mapped_lines,
        "notice": "Proposed bill allocation only. The accounting system determines the final ledger and tax treatment; this does not record a payment.",
        "source": catalogue["source"],
    }


def remember(db, credential, document, body):
    if not body.remember_mapping:
        return
    key = scope(credential, document.seller_id)
    old = db.get(BuyerMapping, key)
    existing = old.mapping if old else {}
    from cbin.connectors.registry import connection_fingerprint

    fingerprint = connection_fingerprint(credential.business_id, credential.environment)
    if existing.get("connection_fingerprint") != fingerprint:
        existing = {}
    accounts = existing.get("account_mapping", {}).copy()
    choices = {}
    for allocation in body.line_allocations:
        code = document.payload["line_items"][allocation.line_index]["item_code"]
        choices.setdefault(code, set()).add(allocation.account_reference)
    for code, selected_accounts in choices.items():
        if len(selected_accounts) == 1:
            accounts[code] = next(iter(selected_accounts))
        else:
            # The same seller item was split between accounts. Ask again next time.
            accounts.pop(code, None)
    mapping = {
        "connection_fingerprint": fingerprint,
        "supplier_reference": body.supplier_reference,
        "account_reference": body.account_reference,
        "sku_mapping": existing.get("sku_mapping", {}) | body.sku_mapping,
        "tax_mapping": existing.get("tax_mapping", {}) | body.tax_mapping,
        "account_mapping": accounts,
    }
    db.merge(
        BuyerMapping(
            scope=key,
            buyer_id=credential.business_id,
            seller_id=document.seller_id,
            environment=credential.environment,
            mapping=mapping,
            updated_at=now(),
        )
    )
    record(db, credential, "bookkeeping.mapping_saved", document, {"seller_id": document.seller_id})
