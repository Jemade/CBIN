import time

import pytest
from fastapi.testclient import TestClient

import cbin.connectors.registry as registry
from cbin.demo import create_demo_app


def test_demo_requires_private_access_and_processes_real_outbox_without_test_endpoints(monkeypatch):
    monkeypatch.setenv("CBIN_ENVIRONMENT", "test")
    monkeypatch.setenv("CBIN_DEMO_ACCESS_KEY", "disposable-demo-access-" + "x" * 32)
    monkeypatch.setattr(registry, "adapter_for", registry.adapter_for)
    with TestClient(create_demo_app()) as client:
        assert client.get("/__demo/session").status_code == 403
        response = client.get(
            "/__demo/session", headers={"X-Demo-Access": "disposable-demo-access-" + "x" * 32}
        )
        assert response.headers["cache-control"] == "no-store"
        keys = response.json()["workspaces"]
        seller = {"Authorization": "Bearer " + keys["Seller"]}
        buyer = {"Authorization": "Bearer " + keys["Buyer"]}
        first = client.post("/v1/seller/source-documents/1/send", headers=seller)
        assert first.status_code == 202
        ident = first.json()["id"]
        for _ in range(100):
            if client.get(f"/v1/documents/{ident}", headers=buyer).json()["status"] == "delivered":
                break
            time.sleep(0.05)
        assert client.get(f"/v1/documents/{ident}", headers=buyer).json()["status"] == "delivered"
        body = {
            "bookkeeping": True,
            "remember_mapping": True,
            "supplier_reference": "SUP-01",
            "account_reference": "INV-GOODS",
            "sku_mapping": {"SKU-1": "ITEM-CABLE"},
            "tax_mapping": {"15": "TAX-15"},
            "line_allocations": [{"line_index": 0, "account_reference": "INV-GOODS"}],
        }
        assert (
            client.post(
                f"/v1/documents/{ident}/bookkeeping/preview", json=body, headers=buyer
            ).status_code
            == 200
        )
        assert (
            client.post(f"/v1/documents/{ident}/accept", json=body, headers=buyer).status_code
            == 200
        )
        for _ in range(100):
            if client.get(f"/v1/documents/{ident}", headers=buyer).json()["status"] == "posted":
                break
            time.sleep(0.05)
        assert (
            client.get(f"/v1/documents/{ident}", headers=buyer).json()["posted_reference"]
            == f"sandbox:{ident}"
        )
        assert (
            client.post("/v1/seller/source-documents/1/send", headers=seller).json()["id"] == ident
        )
        assert client.post("/v1/seller/source-documents/2/send", headers=buyer).status_code == 403
        assert client.post("/__test/reset").status_code == 404
        assert client.post("/__test/drain").status_code == 404


def test_demo_cannot_start_in_live_environment_or_without_access(monkeypatch):
    monkeypatch.setenv("CBIN_ENVIRONMENT", "live")
    with pytest.raises(ValueError, match="live mode"):
        create_demo_app()
    monkeypatch.setenv("CBIN_ENVIRONMENT", "test")
    monkeypatch.delenv("CBIN_DEMO_ACCESS_KEY", raising=False)
    with pytest.raises(ValueError, match="CBIN_DEMO_ACCESS_KEY"):
        create_demo_app()
