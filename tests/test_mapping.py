import copy
import json

import pytest
from conftest import headers

from cbin.connectors.catalogue import catalogue
from cbin.connectors.mapping import MappingError, MappingProfile, normalize


def profile():
    return {
        "reviewed": True,
        "evidence_reference": "TEST-FIXTURE-ONLY",
        "fields": {
            name: {"path": name}
            for name in [
                "document_type",
                "external_reference",
                "issued_at",
                "currency",
                "seller",
                "buyer",
                "totals",
            ]
        },
        "line_items_path": "line_items",
        "line_fields": {
            name: {"path": name}
            for name in ["item_code", "description", "quantity", "unit_price_minor", "tax_rate"]
        },
    }


def test_all_observed_entries_use_reviewed_structured_import(system, invoice, monkeypatch):
    _, client, keys, _ = system
    inventory = client.get("/v1/connectors", headers=headers(keys)).json()
    assert inventory["observed_count"] == len(inventory["items"]) == 34
    profiles = {
        "test": {"seller": {row["id"]: {"test-profile": profile()} for row in inventory["items"]}}
    }
    monkeypatch.setenv("CBIN_IMPORT_PROFILES", json.dumps(profiles))
    # This proves shared configured import coverage, not 34 vendor extraction integrations.
    for row in inventory["items"]:
        payload = copy.deepcopy(invoice)
        payload["external_reference"] = f"TEST-{row['id']}"
        response = client.post(
            f"/v1/connectors/{row['id']}/documents",
            json={"profile_id": "test-profile", "vendor_payload": payload},
            headers=headers(keys, key=f"test-{row['id']}"),
        )
        assert response.status_code == 202, response.text
        assert response.json()["software_id"] == row["id"]
        assert not row["live_verified"]


def test_import_preserves_cross_connector_deduplication(system, invoice, monkeypatch):
    _, client, keys, _ = system
    monkeypatch.setenv(
        "CBIN_IMPORT_PROFILES", json.dumps({"test": {"seller": {"xero": {"reviewed": profile()}}}})
    )
    original = client.post("/v1/documents", json=invoice, headers=headers(keys)).json()
    response = client.post(
        "/v1/connectors/xero/documents",
        json={"profile_id": "reviewed", "vendor_payload": invoice},
        headers=headers(keys, key="other-connector"),
    )
    assert response.status_code == 202
    assert response.json()["duplicate"]
    assert response.json()["id"] == original["id"]


def test_mapping_requires_tenant_environment_and_review(system, invoice, monkeypatch):
    _, client, keys, _ = system
    body = {"profile_id": "test", "vendor_payload": invoice}
    url = "/v1/connectors/xero/documents"
    assert (
        client.post(url, json=body, headers=headers(keys)).json()["error"]["code"]
        == "IMPORT_PROFILE_NOT_CONFIGURED"
    )
    monkeypatch.setenv(
        "CBIN_IMPORT_PROFILES", json.dumps({"live": {"seller": {"xero": {"test": profile()}}}})
    )
    assert client.post(url, json=body, headers=headers(keys)).status_code == 409
    monkeypatch.setenv(
        "CBIN_IMPORT_PROFILES", json.dumps({"test": {"stranger": {"xero": {"test": profile()}}}})
    )
    assert client.post(url, json=body, headers=headers(keys)).status_code == 409
    configured = profile()
    configured["reviewed"] = False
    monkeypatch.setenv(
        "CBIN_IMPORT_PROFILES", json.dumps({"test": {"seller": {"xero": {"test": configured}}}})
    )
    assert (
        client.post(url, json=body, headers=headers(keys)).json()["error"]["code"]
        == "PROFILE_NOT_REVIEWED"
    )
    assert client.post(url, json=body, headers=headers(keys, "seller:reviewer")).status_code == 403
    assert (
        client.post(
            "/v1/connectors/invented/documents", json=body, headers=headers(keys)
        ).status_code
        == 404
    )


def test_mapping_converts_major_units_exactly_and_rejects_loss(invoice):
    configured = profile()
    configured["line_fields"]["unit_price_minor"] = {
        "path": "unit_price",
        "transform": "major_to_minor",
    }
    invoice["line_items"][0]["unit_price"] = "100.00"
    result = normalize(invoice, MappingProfile.model_validate(configured))
    assert result.line_items[0].unit_price_minor == 10000
    for invalid in [100.0, "100.001", "NaN", "Infinity", True]:
        invoice["line_items"][0]["unit_price"] = invalid
        with pytest.raises(MappingError):
            normalize(invoice, MappingProfile.model_validate(configured))


def test_mapping_never_repairs_missing_fields_or_mismatched_totals(invoice):
    configured = MappingProfile.model_validate(profile())
    invoice["totals"]["tax_minor"] = 1
    with pytest.raises(MappingError, match="MAPPED_INVOICE_INVALID"):
        normalize(invoice, configured)
    del invoice["totals"]
    with pytest.raises(MappingError, match="SOURCE_FIELD_MISSING"):
        normalize(invoice, configured)


def test_no_entry_claims_live_posting():
    assert all(not row["live_verified"] for row in catalogue()["items"])
    assert sum(row["native_adapter"] is not None for row in catalogue()["items"]) == 1
