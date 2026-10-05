import json

import httpx
import pytest
from conftest import headers

from cbin.connectors.base import AmbiguousOutcome, ConnectorError
from cbin.connectors.catalogue import catalogue
from cbin.connectors.odoo import OdooAdapter
from cbin.schemas import Invoice

CONFIG = {
    "base_url": "http://odoo-test:8069",
    "database": "buyer",
    "username": "integration",
    "password_env": "ODOO_TEST_PASSWORD",
    "company_id": 1,
    "currency_ids": {"USD": 2},
    "currency_exponents": {"USD": 2},
}


def test_odoo_draft_bill_uses_selected_per_line_accounts_and_checks_recovery(monkeypatch, invoice):
    monkeypatch.setenv("CBIN_ERP_ALLOWLIST", '["http://odoo-test:8069"]')
    monkeypatch.setenv("ODOO_TEST_PASSWORD", "disposable-password")
    calls = []
    saved = []

    def handler(request):
        body = json.loads(request.content)
        args = body["params"]["args"]
        if body["params"]["service"] == "common":
            return httpx.Response(200, json={"result": 7})
        model, method, positional, kwargs = args[3:]
        calls.append((model, method, positional, kwargs))
        assert kwargs["context"]["allowed_company_ids"] == [1]
        if method == "create":
            saved.append(positional[0])
            return httpx.Response(200, json={"result": 99})
        if model == "account.move.line":
            line = saved[0]["invoice_line_ids"][0][2]
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "id": 55,
                            "product_id": [line["product_id"], "Cable"],
                            "account_id": [line["account_id"], "Equipment"],
                            "tax_ids": [9],
                            "quantity": line["quantity"],
                            "price_unit": line["price_unit"],
                            "discount": 0,
                        }
                    ]
                },
            )
        if model == "account.move":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "id": 99,
                            "state": "draft",
                            "partner_id": [3, "Supplier"],
                            "currency_id": [2, "USD"],
                            "amount_total": 230,
                            "invoice_line_ids": [55],
                        }
                    ]
                    if saved
                    else []
                },
            )
        raise AssertionError(method)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = OdooAdapter(CONFIG, client)
        mapping = {
            "supplier_reference": "3",
            "account_reference": "5",
            "sku_mapping": {"SKU-1": "8"},
            "tax_mapping": {"15": "9"},
            "line_allocations": [{"line_index": 0, "account_reference": "6"}],
        }
        assert adapter.recover_from_timeout("D-1", invoice, mapping) is None
        assert adapter.post_to_ledger("D-1", invoice, mapping) == "99"
        assert saved[0]["invoice_line_ids"][0][2]["account_id"] == 6
        assert saved[0]["invoice_line_ids"][0][2]["tax_ids"] == [[6, 0, [9]]]
        assert saved[0]["ref"] == "CBIN-D-1"
        assert adapter.recover_from_timeout("D-1", invoice, mapping) == "99"
        with pytest.raises(AmbiguousOutcome):
            adapter.recover_from_timeout("D-1", invoice, mapping | {"supplier_reference": "999"})
        with pytest.raises(AmbiguousOutcome, match="LINES_MISMATCH"):
            adapter.recover_from_timeout(
                "D-1",
                invoice,
                mapping | {"line_allocations": [{"line_index": 0, "account_reference": "999"}]},
            )
        assert len(saved) == 1


def test_odoo_endpoint_is_explicitly_allowlisted(monkeypatch):
    monkeypatch.setenv("CBIN_ERP_ALLOWLIST", "[]")
    with pytest.raises(ConnectorError, match="ODOO_ENDPOINT_NOT_APPROVED"):
        OdooAdapter(CONFIG)


def test_odoo_uncertain_write_never_returns_success(monkeypatch, invoice):
    monkeypatch.setenv("CBIN_ERP_ALLOWLIST", '["http://odoo-test:8069"]')
    monkeypatch.setenv("ODOO_TEST_PASSWORD", "disposable-password")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: (
                httpx.Response(200, json={"result": 7})
                if json.loads(r.content)["params"]["service"] == "common"
                else httpx.Response(503)
            )
        )
    ) as client:
        adapter = OdooAdapter(CONFIG, client)
        with pytest.raises(AmbiguousOutcome):
            adapter.execute("account.move", "create", [{}], write=True)


def test_seller_source_send_is_authorized_immutable_and_idempotent(system, invoice, monkeypatch):
    class Adapter:
        def source_documents(self):
            return [{"source_id": "1", "reference": invoice["external_reference"]}]

        def source_invoice(self, ident):
            return Invoice.model_validate(invoice)

    monkeypatch.setattr("cbin.connectors.registry.adapter_for", lambda *_: Adapter())
    _, client, keys, _ = system
    path = "/v1/seller/source-documents/1/send"
    assert client.post(path, headers=headers(keys, "seller:reviewer")).status_code == 403
    first = client.post(path, headers=headers(keys))
    assert first.status_code == 202, first.text
    again = client.post(path, headers=headers(keys))
    assert again.status_code == 202
    assert again.json()["duplicate"]
    assert first.json()["id"] == again.json()["id"]
    rows = client.get("/v1/seller/source-documents", headers=headers(keys)).json()["items"]
    assert rows[0]["exchange_status"] == "queued"
    assert (
        client.post("/v1/seller/source-documents/not-an-id/send", headers=headers(keys)).status_code
        == 422
    )


def test_document_direction_is_tenant_scoped(system, invoice):
    _, client, keys, _ = system
    assert client.post("/v1/documents", json=invoice, headers=headers(keys)).status_code == 202
    assert (
        len(client.get("/v1/documents?direction=outgoing", headers=headers(keys)).json()["items"])
        == 1
    )
    assert (
        client.get("/v1/documents?direction=incoming", headers=headers(keys)).json()["items"] == []
    )
    assert (
        len(
            client.get(
                "/v1/documents?direction=incoming", headers=headers(keys, "buyer:admin")
            ).json()["items"]
        )
        == 1
    )
    assert (
        client.get("/v1/documents?direction=outgoing", headers=headers(keys, "buyer:admin")).json()[
            "items"
        ]
        == []
    )
    assert client.get("/v1/documents?direction=wrong", headers=headers(keys)).status_code == 422


@pytest.mark.parametrize(
    "software_id",
    catalogue()["items"],
    ids=lambda software: software["id"],
)
def test_each_catalogue_entry_common_protocol_through_buyer_posting(
    system, invoice, monkeypatch, software_id
):
    from test_mapping import profile

    from cbin.connectors.sandbox import SandboxAdapter
    from cbin.worker import Worker

    app, client, keys, settings = system
    software = software_id["id"]
    monkeypatch.setenv(
        "CBIN_IMPORT_PROFILES",
        json.dumps({"test": {"seller": {software: {"protocol-test": profile()}}}}),
    )
    response = client.post(
        f"/v1/connectors/{software}/documents",
        json={"profile_id": "protocol-test", "vendor_payload": invoice},
        headers=headers(keys),
    )
    assert response.status_code == 202, response.text
    worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
    assert worker.run_once()
    ident = response.json()["id"]
    mapping = {
        "supplier_reference": "SUP-01",
        "account_reference": "EXP-OFFICE",
        "sku_mapping": {"SKU-1": "ITEM-CABLE"},
        "tax_mapping": {"15": "TAX-15"},
    }
    assert (
        client.post(
            f"/v1/documents/{ident}/accept", json=mapping, headers=headers(keys, "buyer:admin")
        ).status_code
        == 200
    )
    assert worker.run_once()
    assert (
        client.get(f"/v1/documents/{ident}", headers=headers(keys, "buyer:admin")).json()[
            "posted_reference"
        ]
        == f"sandbox:{ident}"
    )
