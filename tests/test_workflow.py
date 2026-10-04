import copy
from concurrent.futures import ThreadPoolExecutor

from conftest import headers
from sqlalchemy import func, select

from cbin.connectors.base import AmbiguousOutcome, ConnectorError
from cbin.connectors.sandbox import SandboxAdapter
from cbin.db import Attempt, Credential, Document, Job
from cbin.worker import Worker

MAPPING = {
    "sku_mapping": {"SKU-1": "BUYER-SKU"},
    "supplier_reference": "SUP-1",
    "account_reference": "EXP-1",
}


def create(system, invoice, key="submission-1"):
    _, client, keys, _ = system
    response = client.post("/v1/documents", json=invoice, headers=headers(keys, key=key))
    assert response.status_code == 202, response.text
    return response.json()


def worker(system, adapter=None):
    app, _, _, settings = system
    return Worker(settings, app.state.sessions, lambda *_: adapter or SandboxAdapter())


def test_complete_workflow_and_repeat_accept(system, invoice):
    app, client, keys, _ = system
    document = create(system, invoice)
    assert worker(system).run_once()
    response = client.post(
        f"/v1/documents/{document['id']}/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert worker(system).run_once()
    again = client.post(
        f"/v1/documents/{document['id']}/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
    )
    assert again.status_code == 200
    result = client.get(f"/v1/documents/{document['id']}", headers=headers(keys)).json()
    assert result["status"] == "posted"
    assert result["posted_reference"].startswith("sandbox:")
    assert result["timeline"][-1]["kind"] == "document.posted"
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "post")) == 1


def test_idempotency_and_cross_connector_fingerprint(system, invoice):
    first = create(system, invoice)
    same = create(system, invoice)
    cross = create(system, invoice, "another-connector-key")
    assert same["duplicate"] and cross["duplicate"]
    assert first["id"] == same["id"] == cross["id"]
    invoice["issued_at"] = "2026-10-03"
    _, client, keys, _ = system
    assert (
        client.post("/v1/documents", json=invoice, headers=headers(keys)).json()["error"]["code"]
        == "IDEMPOTENCY_CONFLICT"
    )
    assert (
        client.post("/v1/documents", json=invoice, headers=headers(keys, key="third-key")).json()[
            "error"
        ]["code"]
        == "TRANSACTION_CONFLICT"
    )


def test_tenant_and_role_boundaries(system, invoice):
    _, client, keys, _ = system
    document = create(system, invoice)
    assert (
        client.get(
            f"/v1/documents/{document['id']}", headers=headers(keys, "stranger:admin")
        ).status_code
        == 404
    )
    assert (
        client.get("/v1/documents", headers=headers(keys, "stranger:admin")).json()["items"] == []
    )
    assert client.get("/v1/events", headers=headers(keys, "stranger:admin")).json()[
        "items"
    ] == [] or all(
        e["document_id"] is None
        for e in client.get("/v1/events", headers=headers(keys, "stranger:admin")).json()["items"]
    )
    assert (
        client.post(
            "/v1/documents", json=invoice, headers=headers(keys, "seller:reviewer")
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/documents", json=invoice, headers=headers(keys, "stranger:admin")
        ).status_code
        == 403
    )
    assert client.get("/v1/operations/jobs", headers=headers(keys)).status_code == 403
    assert client.get("/v1/documents").status_code == 401


def test_validation_and_identity(system, invoice):
    _, client, keys, _ = system
    altered = copy.deepcopy(invoice)
    altered["totals"]["grand_total_minor"] = 23001
    assert client.post("/v1/documents", json=altered, headers=headers(keys)).status_code == 422
    for party in [
        {"cbin_id": "unverified", "tin": "TIN-unverified"},
        {"cbin_id": "live-buyer", "tin": "TIN-live"},
    ]:
        altered = copy.deepcopy(invoice)
        altered["buyer"] = party
        assert (
            client.post("/v1/documents", json=altered, headers=headers(keys)).json()["error"][
                "code"
            ]
            == "IDENTITY_UNVERIFIED"
        )
    altered = copy.deepcopy(invoice)
    altered["line_items"][0]["unit_price_minor"] = 10000.0
    assert client.post("/v1/documents", json=altered, headers=headers(keys)).status_code == 422
    altered = copy.deepcopy(invoice)
    altered["issued_at"] = "2026-02-30"
    assert client.post("/v1/documents", json=altered, headers=headers(keys)).status_code == 422


def test_state_mapping_and_rejection(system, invoice):
    _, client, keys, _ = system
    document = create(system, invoice)
    route = f"/v1/documents/{document['id']}"
    assert (
        client.post(
            route + "/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
        ).status_code
        == 409
    )
    worker(system).run_once()
    incomplete = {**MAPPING, "sku_mapping": {"WRONG": "SKU"}}
    assert (
        client.post(
            route + "/accept", json=incomplete, headers=headers(keys, "buyer:admin")
        ).status_code
        == 422
    )
    assert (
        client.post(
            route + "/reject", json={"reason": "Incorrect quantity"}, headers=headers(keys)
        ).status_code
        == 403
    )
    assert (
        client.post(
            route + "/reject",
            json={"reason": "Incorrect quantity"},
            headers=headers(keys, "buyer:admin"),
        ).json()["status"]
        == "rejected_by_buyer"
    )
    assert (
        client.post(
            route + "/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
        ).status_code
        == 409
    )
    assert not worker(system).run_once()


def test_revoked_credentials(system):
    app, client, keys, _ = system
    with app.state.sessions.begin() as db:
        db.get(Credential, keys["seller:admin"]["credential_id"]).revoked = True
    assert client.get("/v1/documents", headers=headers(keys)).status_code == 401


class UncertainAdapter:
    calls = 0

    def recover_from_timeout(self, document_id, *context):
        return None

    def post_to_ledger(self, *args):
        self.calls += 1
        raise AmbiguousOutcome("TEST_TIMEOUT")


def test_post_timeout_never_blindly_retries(system, invoice):
    app, client, keys, _ = system
    document = create(system, invoice)
    worker(system).run_once()
    client.post(
        f"/v1/documents/{document['id']}/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
    )
    adapter = UncertainAdapter()
    instance = worker(system, adapter)
    instance.run_once()
    assert not instance.run_once()
    assert adapter.calls == 1
    with app.state.sessions() as db:
        job = db.scalar(select(Job).where(Job.kind == "post"))
        assert job.state == "needs_reconciliation"
        assert db.get(Document, document["id"]).status == "accepted"
    assert (
        client.post(
            f"/v1/operations/jobs/{job.id}/replay",
            json={"reason": "retry"},
            headers=headers(keys, "buyer:operator"),
        ).status_code
        == 409
    )


def test_crash_lease_recovers_without_duplicate_write(system, invoice):
    app, client, keys, _ = system
    document = create(system, invoice)
    instance = worker(system)
    instance.run_once()
    client.post(
        f"/v1/documents/{document['id']}/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
    )
    claimed = instance.claim()
    with app.state.sessions.begin() as db:
        db.get(Job, claimed[0]).lease_until = 0
    adapter = UncertainAdapter()
    worker(system, adapter).run_once()
    assert adapter.calls == 0
    with app.state.sessions() as db:
        assert db.get(Job, claimed[0]).state == "needs_reconciliation"
        assert (
            db.scalar(select(func.count()).select_from(Attempt).where(Attempt.job_id == claimed[0]))
            == 2
        )


def test_concurrent_submission_creates_one_document(system, invoice):
    app, client, keys, _ = system

    def request(_):
        return client.post("/v1/documents", json=invoice, headers=headers(keys))

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(request, range(4)))
    assert all(r.status_code in {202, 409} for r in responses)
    assert any(r.status_code == 202 for r in responses)
    assert create(system, invoice)["duplicate"]
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "route")) == 1


def test_dead_letter_and_audited_replay(system, invoice):
    app, client, keys, settings = system
    document = create(system, invoice)
    worker(system).run_once()
    client.post(
        f"/v1/documents/{document['id']}/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
    )

    class MissingConfig:
        def recover_from_timeout(self, _, *context):
            raise ConnectorError("NOT_CONFIGURED")

    instance = worker(system, MissingConfig())
    for _ in range(settings.max_attempts):
        with app.state.sessions.begin() as db:
            db.scalar(select(Job).where(Job.kind == "post")).available_at = 0
        instance.run_once()
    with app.state.sessions() as db:
        job = db.scalar(select(Job).where(Job.kind == "post"))
        assert job.state == "dead_letter"
    response = client.post(
        f"/v1/operations/jobs/{job.id}/replay",
        json={"reason": "Connector configuration corrected"},
        headers=headers(keys, "buyer:operator"),
    )
    assert response.status_code == 200
    assert worker(system).run_once()


def test_operator_reconciliation_remains_lookup_only(system, invoice):
    app, client, keys, _ = system
    document = create(system, invoice)
    worker(system).run_once()
    client.post(
        f"/v1/documents/{document['id']}/accept", json=MAPPING, headers=headers(keys, "buyer:admin")
    )
    adapter = UncertainAdapter()
    instance = worker(system, adapter)
    instance.run_once()
    with app.state.sessions() as db:
        job_id = db.scalar(select(Job.id).where(Job.kind == "post"))
    response = client.post(
        f"/v1/operations/jobs/{job_id}/reconcile",
        json={"reason": "Provider timeout investigated"},
        headers=headers(keys, "buyer:operator"),
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "lookup_only"
    instance.run_once()
    assert adapter.calls == 1
    with app.state.sessions() as db:
        assert db.get(Job, job_id).state == "needs_reconciliation"
