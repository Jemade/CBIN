import copy

import httpx
import pytest
from conftest import headers

from cbin.connectors.base import AmbiguousOutcome, ConnectorError
from cbin.connectors.offline import OfflineSpool
from cbin.connectors.registry import adapter_for
from cbin.connectors.zoho import ZohoBooksAdapter
from cbin.security import sign_event, verify_event


def test_signature_replay_window_and_tamper():
    signature = sign_event("secret", 1000, b'{"id":1}')
    assert verify_event("secret", 1000, b'{"id":1}', signature, 1100)
    assert not verify_event("secret", 1000, b'{"id":2}', signature, 1100)
    assert not verify_event("secret", 1000, b'{"id":1}', signature, 1400)


def test_offline_spool_reconnect_and_payload_conflict(tmp_path, invoice):
    path = tmp_path / "spool.db"
    spool = OfflineSpool(path)
    spool.enqueue("sale-1", invoice)
    altered = copy.deepcopy(invoice)
    altered["issued_at"] = "2026-10-03"
    with pytest.raises(ValueError):
        spool.enqueue("sale-1", altered)
    spool.close()
    spool = OfflineSpool(path)

    def fail(request):
        raise httpx.ConnectError("offline")

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        assert not spool.flush_one(client, "https://cbin.example", "key")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(202, json={"id": "doc"}))
    ) as client:
        assert spool.flush_one(client, "https://cbin.example", "key")
        assert not spool.flush_one(client, "https://cbin.example", "key")
    spool.close()


def test_zoho_draft_mapping_and_recovery(monkeypatch, invoice):
    monkeypatch.setenv("TEST_ZOHO_TOKEN", "test-token")
    captured = []

    def handler(request):
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "bills": []})
        return httpx.Response(
            201, json={"code": 0, "bill": {"bill_id": "B-1", "status": "draft", "total": "230.00"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = ZohoBooksAdapter(
            {
                "token_env": "TEST_ZOHO_TOKEN",
                "organization_id": "ORG",
                "currency_ids": {"USD": "CUR-USD"},
                "currency_exponents": {"USD": 2},
            },
            client,
        )
        assert adapter.recover_from_timeout("D-1") is None
        reference = adapter.post_to_ledger(
            "D-1",
            invoice,
            {
                "sku_mapping": {"SKU-1": "ITEM-1"},
                "supplier_reference": "VENDOR",
                "account_reference": "ACCOUNT",
                "tax_mapping": {"15": "TAX"},
            },
        )
        assert reference == "B-1"
        import json

        payload = json.loads(captured[-1].content)
        assert payload["reference_number"] == "CBIN-D-1"
        assert payload["line_items"][0]["rate"] == 100


def test_zoho_uncertain_server_response(monkeypatch):
    monkeypatch.setenv("TEST_ZOHO_TOKEN", "test-token")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        adapter = ZohoBooksAdapter(
            {"token_env": "TEST_ZOHO_TOKEN", "organization_id": "ORG"}, client
        )
        with pytest.raises(AmbiguousOutcome):
            adapter.recover_from_timeout("D-1")


def test_live_posting_is_gated(monkeypatch):
    monkeypatch.setenv("CBIN_CONNECTOR_CONFIG", '{"buyer":{"type":"sandbox"}}')
    with pytest.raises(ConnectorError):
        adapter_for("buyer", "live")


def test_webhook_registration_rbac_and_url_validation(system):
    _, client, keys, _ = system
    body = {"url": "https://receiver.example/events", "secret_ref": "CBIN_WEBHOOK_TEST"}
    assert (
        client.post(
            "/v1/webhook-endpoints", json=body, headers=headers(keys, "buyer:admin")
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/v1/webhook-endpoints", json=body, headers=headers(keys, "seller:reviewer")
        ).status_code
        == 403
    )
    for url in [
        "http://receiver.example",
        "https://user:pass@receiver.example",
        "https://receiver.example/#fragment",
    ]:
        assert (
            client.post(
                "/v1/webhook-endpoints", json={**body, "url": url}, headers=headers(keys)
            ).status_code
            == 422
        )


def test_webhook_dead_letters_do_not_spawn_recursive_notifications(system, invoice):
    from sqlalchemy import func, select

    from cbin.db import Job
    from cbin.worker import Worker

    app, client, keys, settings = system
    client.post(
        "/v1/webhook-endpoints",
        json={"url": "https://receiver.example/events", "secret_ref": "CBIN_WEBHOOK_TEST"},
        headers=headers(keys, "buyer:admin"),
    )
    assert client.post("/v1/documents", json=invoice, headers=headers(keys)).status_code == 202
    instance = Worker(settings, app.state.sessions)
    # No egress approval: every webhook must exhaust bounded attempts without recursive jobs.
    with app.state.sessions() as db:
        initial = db.scalar(select(func.count()).select_from(Job).where(Job.kind == "webhook"))
        assert initial == 3
    for _ in range(100):
        with app.state.sessions.begin() as db:
            for job in db.scalars(select(Job).where(Job.state == "pending")):
                job.available_at = 0
        if not instance.run_once():
            break
    else:
        raise AssertionError("Queue did not drain")
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "webhook")) == 4
        assert (
            db.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.kind == "webhook", Job.state == "dead_letter")
            )
            == 4
        )
