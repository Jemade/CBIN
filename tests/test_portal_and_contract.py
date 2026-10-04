import copy

from conftest import headers


def test_portal_assets_and_contract(system):
    _, client, _, _ = system
    for path, content in [
        ("/", "Business Interoperability Network"),
        ("/portal.js", "use strict"),
        ("/portal.css", ":root"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert content in response.text
    schema = client.get("/openapi.json").json()
    assert "/v1/documents/{document_id}/accept" in schema["paths"]
    assert client.get("/health/ready").status_code == 200


def test_request_limits_and_error_redaction(system):
    _, client, keys, _ = system
    response = client.post("/v1/documents", content=b"x" * 2_000_001, headers=headers(keys))
    assert response.status_code == 413
    response = client.post(
        "/v1/documents", json={"secret": "sensitive-input"}, headers=headers(keys)
    )
    assert response.status_code == 422
    assert "sensitive-input" not in response.text
    assert response.json()["error"]["request_id"]
    assert client.get("/v1/events?cursor=invalid!", headers=headers(keys)).status_code == 422


def test_linked_credit_notes_and_event_cursor(system, invoice):
    _, client, keys, _ = system
    original = client.post("/v1/documents", json=invoice, headers=headers(keys)).json()
    credit = copy.deepcopy(invoice)
    credit.update(
        document_type="CREDIT_NOTE", external_reference="CREDIT-001", correction_of=original["id"]
    )
    response = client.post("/v1/documents", json=credit, headers=headers(keys, key="credit-001"))
    assert response.status_code == 202
    first = client.get("/v1/events?limit=2", headers=headers(keys)).json()
    assert first["next_cursor"]
    second = client.get(
        "/v1/events?limit=2&cursor=" + first["next_cursor"], headers=headers(keys)
    ).json()
    assert first["items"][-1]["id"] < second["items"][0]["id"]
    credit["currency"] = "ZWG"
    assert (
        client.post(
            "/v1/documents", json=credit, headers=headers(keys, key="credit-bad")
        ).status_code
        == 422
    )
