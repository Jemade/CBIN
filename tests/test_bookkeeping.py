import copy
import json

import httpx
import pytest
from conftest import headers
from sqlalchemy import func, select

from cbin.connectors.base import ConnectorError
from cbin.connectors.sandbox import SandboxAdapter
from cbin.connectors.zoho import ZohoBooksAdapter
from cbin.db import AuditEvent, BuyerMapping, Decision, Document
from cbin.worker import Worker

CHOICE = {
    "supplier_reference": "SUP-01",
    "account_reference": "EXP-OFFICE",
    "sku_mapping": {"SKU-1": "ITEM-CABLE"},
    "tax_mapping": {"15": "TAX-15"},
    "line_allocations": [{"line_index": 0, "account_reference": "EXP-OFFICE"}],
    "bookkeeping": True,
    "remember_mapping": True,
    "purchase_order_reference": "PO-001",
}


@pytest.fixture
def accounting(monkeypatch):
    monkeypatch.setenv("CBIN_CONNECTOR_CONFIG", json.dumps({"buyer": {"type": "sandbox"}}))


def delivered(system, invoice, reference="INV-001"):
    app, client, keys, settings = system
    body = copy.deepcopy(invoice)
    body["external_reference"] = reference
    response = client.post("/v1/documents", json=body, headers=headers(keys, key=reference))
    assert response.status_code == 202, response.text
    worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
    while worker.run_once():
        pass
    return response.json()["id"]


def test_bookkeeping_preview_approval_post_and_reuse(system, invoice, accounting):
    app, client, keys, settings = system
    ident = delivered(system, invoice)
    url = f"/v1/documents/{ident}"
    auth = headers(keys, "buyer:admin")
    context = client.get(url + "/bookkeeping", headers=auth)
    assert context.status_code == 200
    assert context.json()["references"]["source"] == "sandbox_simulation"
    assert not context.json()["suggestions"]["saved"]
    preview = client.post(url + "/bookkeeping/preview", json=CHOICE, headers=auth)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert [(r["account_id"], r["debit_minor"], r["credit_minor"]) for r in data["entries"]] == [
        ("EXP-OFFICE", 20000, 0),
        ("TAX-INPUT", 3000, 0),
        ("AP", 0, 23000),
    ]
    assert data["debit_minor"] == data["credit_minor"] == 23000
    assert data["purchase_order"]["status"] == "supplier_currency_total_match"
    with app.state.sessions() as db:
        assert db.get(Document, ident).status == "delivered"
        assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert client.post(url + "/accept", json=CHOICE, headers=auth).status_code == 200
    assert client.post(url + "/accept", json=CHOICE, headers=auth).status_code == 200
    assert Worker(settings, app.state.sessions, lambda *_: SandboxAdapter()).run_once()
    assert client.get(url, headers=auth).json()["posted_reference"] == f"sandbox:{ident}"
    second = delivered(system, invoice, "INV-002")
    suggestions = client.get(f"/v1/documents/{second}/bookkeeping", headers=auth).json()[
        "suggestions"
    ]
    assert suggestions["saved"]
    assert suggestions["supplier_reference"] == "SUP-01"
    assert suggestions["sku_mapping"] == {"SKU-1": "ITEM-CABLE"}
    assert suggestions["line_allocations"] == CHOICE["line_allocations"]
    assert "purchase_order_reference" not in suggestions
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BuyerMapping)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.kind == "bookkeeping.mapping_saved")
            )
            == 1
        )


@pytest.mark.parametrize(
    "change,code",
    [
        ({"supplier_reference": "FOREIGN"}, "ACCOUNTING_SELECTION_INVALID"),
        ({"account_reference": "OTHER"}, "ACCOUNTING_SELECTION_INVALID"),
        ({"sku_mapping": {"SKU-1": "MISSING"}}, "ACCOUNTING_SELECTION_INVALID"),
        ({"tax_mapping": {"15": "TAX-0"}}, "TAX_RATE_MISMATCH"),
        ({"tax_mapping": {}}, "TAX_MAPPING_INCOMPLETE"),
        ({"line_allocations": []}, "ALLOCATION_INCOMPLETE"),
        (
            {"line_allocations": [{"line_index": 0, "account_reference": "EXP-OFFICE"}] * 2},
            "ALLOCATION_INCOMPLETE",
        ),
        ({"supplier_reference": "SUP-02"}, "PURCHASE_ORDER_MISMATCH"),
    ],
)
def test_invalid_selection_cannot_be_approved(system, invoice, accounting, change, code):
    app, client, keys, _ = system
    ident = delivered(system, invoice)
    for endpoint in ["/bookkeeping/preview", "/accept"]:
        response = client.post(
            f"/v1/documents/{ident}" + endpoint,
            json=CHOICE | change,
            headers=headers(keys, "buyer:admin"),
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == code
    with app.state.sessions() as db:
        assert db.get(Document, ident).status == "delivered"
        assert db.get(Decision, ident) is None
        assert db.scalar(select(func.count()).select_from(BuyerMapping)) == 0


def test_line_allocations_and_exact_half_up_rounding(system, invoice, accounting):
    invoice["line_items"] = [
        {
            "item_code": "SKU-1",
            "description": "Fractional item",
            "quantity": "0.5",
            "unit_price_minor": 101,
            "tax_rate": "15",
        },
        {
            "item_code": "SKU-2",
            "description": "Equipment",
            "quantity": "1",
            "unit_price_minor": 201,
            "tax_rate": "0",
        },
    ]
    invoice["totals"] = {"subtotal_minor": 252, "tax_minor": 8, "grand_total_minor": 260}
    ident = delivered(system, invoice)
    choice = CHOICE | {
        "purchase_order_reference": None,
        "sku_mapping": {"SKU-1": "ITEM-CABLE", "SKU-2": "ITEM-OFFICE"},
        "tax_mapping": {"15": "TAX-15", "0": "TAX-0"},
        "line_allocations": [
            {"line_index": 0, "account_reference": "EXP-OFFICE"},
            {"line_index": 1, "account_reference": "ASSET-EQUIPMENT"},
        ],
    }
    response = system[1].post(
        f"/v1/documents/{ident}/bookkeeping/preview",
        json=choice,
        headers=headers(system[2], "buyer:admin"),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["debit_minor"] == data["credit_minor"] == 260
    assert {r["account_id"]: r["debit_minor"] for r in data["entries"]} == {
        "EXP-OFFICE": 51,
        "TAX-INPUT": 8,
        "ASSET-EQUIPMENT": 201,
        "AP": 0,
    }


def test_access_scope_opt_out_and_connector_change(system, invoice, accounting, monkeypatch):
    app, client, keys, _ = system
    ident = delivered(system, invoice)
    url = f"/v1/documents/{ident}"
    assert client.get(url + "/bookkeeping").status_code == 401
    assert client.get(url + "/bookkeeping", headers=headers(keys)).status_code == 403
    assert (
        client.get(url + "/bookkeeping", headers=headers(keys, "stranger:admin")).status_code == 404
    )
    assert (
        client.get(url + "/bookkeeping", headers=headers(keys, "buyer:operator")).status_code == 403
    )
    assert (
        client.post(
            url + "/accept",
            json=CHOICE | {"remember_mapping": False},
            headers=headers(keys, "buyer:admin"),
        ).status_code
        == 200
    )
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BuyerMapping)) == 0
    second = delivered(system, invoice, "INV-002")
    assert (
        client.post(
            f"/v1/documents/{second}/accept", json=CHOICE, headers=headers(keys, "buyer:admin")
        ).status_code
        == 200
    )
    monkeypatch.setenv(
        "CBIN_CONNECTOR_CONFIG",
        json.dumps({"buyer": {"type": "sandbox", "organization_id": "another-org"}}),
    )
    third = delivered(system, invoice, "INV-003")
    data = client.get(
        f"/v1/documents/{third}/bookkeeping", headers=headers(keys, "buyer:admin")
    ).json()
    assert not data["suggestions"]["saved"]
    assert data["suggestions"]["supplier_reference"] == ""


def test_missing_connection_is_explicit(system, invoice):
    ident = delivered(system, invoice)
    response = system[1].get(
        f"/v1/documents/{ident}/bookkeeping", headers=headers(system[2], "buyer:admin")
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACCOUNTING_UNAVAILABLE"


def test_stale_records_are_not_suggested(system, invoice, accounting, monkeypatch):
    _, client, keys, _ = system
    ident = delivered(system, invoice)
    auth = headers(keys, "buyer:admin")
    assert (
        client.post(f"/v1/documents/{ident}/accept", json=CHOICE, headers=auth).status_code == 200
    )
    second = delivered(system, invoice, "INV-002")
    original = SandboxAdapter.accounting_references

    def changed(self):
        data = original(self)
        data["items"] = []
        return data

    monkeypatch.setattr(SandboxAdapter, "accounting_references", changed)
    assert client.post("/v1/accounting/references/refresh", headers=auth).status_code == 200
    context = client.get(f"/v1/documents/{second}/bookkeeping", headers=auth).json()
    assert context["suggestions"]["sku_mapping"] == {"SKU-1": ""}
    assert (
        client.post(f"/v1/documents/{second}/accept", json=CHOICE, headers=auth).status_code == 422
    )


def test_zoho_reference_pagination_and_per_line_posting(monkeypatch, invoice):
    monkeypatch.setenv("TEST_ZOHO_TOKEN", "test-token")
    posts = []

    def handle(request):
        path = request.url.path
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(
                201,
                json={"code": 0, "bill": {"bill_id": "B1", "status": "draft", "total": "230.00"}},
            )
        if path.endswith("/contacts"):
            page = request.url.params["page"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "contacts": [
                        {
                            "contact_id": "SUP-" + page,
                            "contact_name": "Supplier " + page,
                            "status": "active",
                        }
                    ],
                    "page_context": {"has_more_page": page == "1"},
                },
            )
        if path.endswith("/chartofaccounts"):
            rows = [
                {
                    "account_id": "EXP-OFFICE",
                    "account_name": "Office supplies",
                    "account_type": "expense",
                    "is_active": True,
                },
                {
                    "account_id": "AP",
                    "account_name": "Payables",
                    "account_type": "accounts_payable",
                    "is_active": True,
                },
                {
                    "account_id": "OLD",
                    "account_name": "Inactive",
                    "account_type": "expense",
                    "is_active": False,
                },
            ]
            return httpx.Response(200, json={"code": 0, "chartofaccounts": rows})
        if path.endswith("/items"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "items": [{"item_id": "ITEM-CABLE", "name": "Cable", "status": "active"}],
                },
            )
        if path.endswith("/settings/taxes"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "taxes": [{"tax_id": "TAX-15", "tax_name": "Tax 15", "tax_percentage": 15}],
                },
            )
        raise AssertionError(path)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        adapter = ZohoBooksAdapter(
            {
                "token_env": "TEST_ZOHO_TOKEN",
                "organization_id": "ORG",
                "currency_ids": {"USD": "USD-ID"},
                "currency_exponents": {"USD": 2},
            },
            client,
        )
        data = adapter.accounting_references()
        assert len(data["suppliers"]) == 2
        assert len(data["accounts"]) == 1
        assert data["payable_account"]["id"] == "AP"
        mapping = CHOICE | {
            "account_reference": "DEFAULT",
            "line_allocations": [{"line_index": 0, "account_reference": "PER-LINE"}],
        }
        assert adapter.post_to_ledger("DOC", invoice, mapping) == "B1"
        assert posts[0]["line_items"][0]["account_id"] == "PER-LINE"
        assert "PO-001" in posts[0]["notes"]
        assert "INV-001" in posts[0]["notes"]


def test_zoho_lookup_failure_does_not_supply_fake_records(monkeypatch):
    monkeypatch.setenv("TEST_ZOHO_TOKEN", "test-token")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client:
        adapter = ZohoBooksAdapter(
            {"token_env": "TEST_ZOHO_TOKEN", "organization_id": "ORG"}, client
        )
        with pytest.raises(ConnectorError, match="ZOHO_HTTP_403"):
            adapter.accounting_references()


def test_new_items_and_ambiguous_account_splits_require_fresh_choices(system, invoice, accounting):
    _, client, keys, _ = system
    invoice["line_items"] *= 2
    invoice["totals"] = {"subtotal_minor": 40000, "tax_minor": 6000, "grand_total_minor": 46000}
    ident = delivered(system, invoice)
    body = CHOICE | {
        "purchase_order_reference": None,
        "line_allocations": [
            {"line_index": 0, "account_reference": "EXP-OFFICE"},
            {"line_index": 1, "account_reference": "INV-GOODS"},
        ],
    }
    assert (
        client.post(
            f"/v1/documents/{ident}/accept", json=body, headers=headers(keys, "buyer:admin")
        ).status_code
        == 200
    )
    invoice["line_items"][1] = {**invoice["line_items"][1], "item_code": "NEW-SKU"}
    second = delivered(system, invoice, "INV-002")
    suggestions = client.get(
        f"/v1/documents/{second}/bookkeeping", headers=headers(keys, "buyer:admin")
    ).json()["suggestions"]
    assert suggestions["line_allocations"] == [
        {"line_index": 0, "account_reference": ""},
        {"line_index": 1, "account_reference": ""},
    ]
    assert suggestions["sku_mapping"]["NEW-SKU"] == ""


def test_buyer_preferences_are_not_shared_with_another_buyer(
    system, invoice, accounting, monkeypatch
):
    app, client, keys, _ = system
    ident = delivered(system, invoice)
    auth = headers(keys, "buyer:admin")
    assert (
        client.post(f"/v1/documents/{ident}/accept", json=CHOICE, headers=auth).status_code == 200
    )
    monkeypatch.setenv(
        "CBIN_CONNECTOR_CONFIG",
        json.dumps({"buyer": {"type": "sandbox"}, "stranger": {"type": "sandbox"}}),
    )
    invoice["buyer"] = {"cbin_id": "stranger", "tin": "TIN-stranger"}
    other = delivered(system, invoice, "INV-OTHER")
    response = client.get(
        f"/v1/documents/{other}/bookkeeping", headers=headers(keys, "stranger:admin")
    )
    assert response.status_code == 200
    assert not response.json()["suggestions"]["saved"]
    assert response.json()["suggestions"]["supplier_reference"] == ""
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BuyerMapping)) == 1


def test_credit_note_preview_reverses_entry_and_unsupported_currency_fails(
    system, invoice, accounting
):
    _, client, keys, _ = system
    original = delivered(system, invoice)
    credit = {**invoice, "document_type": "CREDIT_NOTE", "correction_of": original}
    ident = delivered(system, credit, "CREDIT-001")
    response = client.post(
        f"/v1/documents/{ident}/bookkeeping/preview",
        json=CHOICE | {"purchase_order_reference": None},
        headers=headers(keys, "buyer:admin"),
    )
    assert response.status_code == 200
    assert response.json()["entries"][0]["credit_minor"] == 20000
    assert response.json()["entries"][-1]["debit_minor"] == 23000
    invoice["currency"] = "JPY"
    foreign = delivered(system, invoice, "INV-JPY")
    response = client.post(
        f"/v1/documents/{foreign}/bookkeeping/preview",
        json=CHOICE,
        headers=headers(keys, "buyer:admin"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CURRENCY_UNSUPPORTED"


def test_purchase_order_total_difference_blocks_matching(system, invoice, accounting):
    invoice["line_items"][0]["unit_price_minor"] = 20000
    invoice["totals"] = {"subtotal_minor": 40000, "tax_minor": 6000, "grand_total_minor": 46000}
    ident = delivered(system, invoice)
    response = system[1].post(
        f"/v1/documents/{ident}/bookkeeping/preview",
        json=CHOICE,
        headers=headers(system[2], "buyer:admin"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PURCHASE_ORDER_MISMATCH"


def test_bookkeeping_migration_is_additive_and_repeatable(system, invoice):
    from cbin.db import AccountingSnapshot

    app, _, _, _ = system
    ident = delivered(system, invoice)
    AccountingSnapshot.__table__.drop(app.state.engine)
    BuyerMapping.__table__.drop(app.state.engine)
    for _ in range(2):
        for table in (AccountingSnapshot.__table__, BuyerMapping.__table__):
            table.create(app.state.engine, checkfirst=True)
    with app.state.sessions() as db:
        assert db.get(Document, ident).status == "delivered"
        assert db.scalar(select(func.count()).select_from(BuyerMapping)) == 0
