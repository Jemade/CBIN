import base64
import hashlib
import io
import json

import httpx
import pytest
from conftest import headers
from pypdf import PdfReader
from sqlalchemy import select

from cbin.capture import extract
from cbin.connectors.base import AmbiguousOutcome
from cbin.connectors.odoo import OdooAdapter
from cbin.connectors.sandbox import SandboxAdapter
from cbin.connectors.zoho import ZohoBooksAdapter
from cbin.db import BillAttachment, Credential, Document, InvoiceFile, Job
from cbin.documents import evidence_packet, invoice_pdf, retain_until
from cbin.fiscal import verify
from cbin.schemas import AttachmentUpload
from cbin.service import DomainError
from cbin.worker import Worker


def original(invoice):
    return {
        "filename": "supplier.pdf",
        "media_type": "application/pdf",
        "content_base64": base64.b64encode(invoice_pdf(invoice)).decode(),
    }


def submitted(system, invoice):
    app, client, keys, settings = system
    response = client.post(
        "/v1/seller/invoice-packages",
        json={"invoice": invoice, "original": original(invoice)},
        headers=headers(keys),
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_package_original_and_archive_are_atomic_idempotent_scoped_and_retained(system, invoice):
    app, client, keys, settings = system
    ident = submitted(system, invoice)
    path = f"/v1/documents/{ident}"
    detail = client.get(path, headers=headers(keys, "buyer:admin")).json()
    assert {f["kind"] for f in detail["files"]} == {"canonical", "exchange_copy", "seller_original"}
    file = next(f for f in detail["files"] if f["kind"] == "seller_original")
    content = client.get(path + "/files/" + file["id"], headers=headers(keys, "buyer:admin"))
    assert content.status_code == 200
    assert hashlib.sha256(content.content).hexdigest() == file["sha256"]
    assert content.headers["cache-control"] == "no-store"
    assert file["retain_until"] >= file["created_at"] + 6 * 365 * 86400
    assert (
        client.get(
            path + "/files/" + file["id"], headers=headers(keys, "stranger:admin")
        ).status_code
        == 404
    )
    body = {"invoice": invoice, "original": original(invoice)}
    assert client.post("/v1/seller/invoice-packages", json=body, headers=headers(keys)).json()[
        "duplicate"
    ]
    body["original"]["content_base64"] = base64.b64encode(b"%PDF-different").decode()
    assert (
        client.post("/v1/seller/invoice-packages", json=body, headers=headers(keys)).status_code
        == 409
    )
    with app.state.sessions() as db:
        assert len(db.scalars(select(Document)).all()) == 1


def test_invalid_original_rolls_back_entire_package(system, invoice):
    app, client, keys, _ = system
    body = original(invoice) | {
        "content_base64": base64.b64encode(b"<script>bad</script>").decode()
    }
    assert (
        client.post(
            "/v1/seller/invoice-packages",
            json={"invoice": invoice, "original": body},
            headers=headers(keys),
        ).status_code
        == 422
    )
    with app.state.sessions() as db:
        assert db.scalar(select(Document)) is None


def test_original_evidence_seals_after_decision_and_rejects_cross_document_reads(system, invoice):
    app, client, keys, settings = system
    ident = submitted(system, invoice)
    worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
    worker.run_once()
    path = f"/v1/documents/{ident}"
    assert (
        client.post(
            path + "/files", json=original(invoice), headers=headers(keys, "seller:reviewer")
        ).status_code
        == 403
    )
    assert (
        client.post(
            path + "/reject",
            json={"reason": "Delivery does not match"},
            headers=headers(keys, "buyer:admin"),
        ).status_code
        == 200
    )
    assert (
        client.post(path + "/files", json=original(invoice), headers=headers(keys)).status_code
        == 409
    )
    assert (
        client.get(path + "/files/unknown", headers=headers(keys, "buyer:admin")).status_code == 404
    )


@pytest.mark.parametrize("layout", ["a4", "receipt48", "escpos32", "escpos48"])
def test_print_formats_keep_totals_and_do_not_issue_a_fiscal_invoice(system, invoice, layout):
    _, client, keys, _ = system
    invoice["line_items"][0]["description"] = "Cable\x1b@<b>test</b>"
    ident = submitted(system, invoice)
    response = client.get(f"/v1/documents/{ident}/print?layout={layout}", headers=headers(keys))
    assert response.status_code == 200
    if layout.startswith("escpos"):
        assert response.content.startswith(b"\x1b@")
        assert response.content.count(b"\x1b@") == 1
        assert b"USD 230.00" in response.content
        assert b"INVOICE EXCHANGE COPY" in response.content
    else:
        text = "".join(
            page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages
        )
        assert "USD 230.00" in text
        assert "Not an original fiscal tax invoice" in text


def test_bad_fiscal_qr_destination_is_rejected(system, invoice):
    _, client, keys, _ = system
    invoice["fiscal_metadata"] = {
        "device_serial": "D1",
        "zimra_signature": "unverified",
        "verification_url": "https://attacker.test/steal",
    }
    assert client.post("/v1/documents", json=invoice, headers=headers(keys)).status_code == 422


def test_erp_attachments_follow_bill_creation_and_do_not_recreate_the_bill(system, invoice):
    app, client, keys, settings = system
    ident = submitted(system, invoice)
    worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
    worker.run_once()
    body = {
        "supplier_reference": "SUP-01",
        "account_reference": "INV-GOODS",
        "sku_mapping": {"SKU-1": "ITEM-CABLE"},
        "tax_mapping": {"15": "TAX-15"},
    }
    assert (
        client.post(
            f"/v1/documents/{ident}/accept", json=body, headers=headers(keys, "buyer:admin")
        ).status_code
        == 200
    )
    while worker.run_once():
        pass
    detail = client.get(f"/v1/documents/{ident}", headers=headers(keys, "buyer:admin")).json()
    assert detail["status"] == "posted"
    assert all(file["erp_status"] == "stored" for file in detail["files"])
    with app.state.sessions() as db:
        assert len(db.scalars(select(Job).where(Job.kind == "post")).all()) == 1
        assert len(db.scalars(select(BillAttachment)).all()) == 3


def test_pdf_packet_embeds_exact_original_and_manifest(system, invoice):
    app, _, _, _ = system
    ident = submitted(system, invoice)
    with app.state.sessions.begin() as db:
        packet = evidence_packet(db, db.get(Document, ident))
        reader = PdfReader(io.BytesIO(packet.content))
        assert "cbin-manifest.json" in reader.attachments
        manifest = json.loads(reader.attachments["cbin-manifest.json"][0])
        for file in manifest:
            assert (
                hashlib.sha256(reader.attachments[file["embedded_name"]][0]).hexdigest()
                == file["sha256"]
            )
        assert evidence_packet(db, db.get(Document, ident)).id == packet.id


def configured(monkeypatch, name):
    monkeypatch.setenv(
        f"CBIN_{name}_CONFIG" if name == "OCR" else "CBIN_FISCAL_VERIFIER_CONFIG",
        json.dumps(
            {
                "test": {
                    "reviewed": True,
                    "provider": "contract-test-only",
                    "evidence_reference": "TEST-CONTRACT",
                    "url": "https://approved.test/check",
                    "token_env": "TEST_PROVIDER_TOKEN",
                }
            }
        ),
    )
    monkeypatch.setenv(
        f"CBIN_{name}_ALLOWLIST" if name == "OCR" else "CBIN_FISCAL_VERIFIER_ALLOWLIST",
        '["https://approved.test/check"]',
    )
    monkeypatch.setenv("TEST_PROVIDER_TOKEN", "disposable-test-token")


def test_fiscal_result_must_match_complete_invoice_and_gate_approval(system, invoice, monkeypatch):
    app, client, keys, settings = system
    invoice["fiscal_metadata"] = {
        "device_serial": "D1",
        "zimra_signature": "unverified",
        "receipt_reference": "F1",
    }
    ident = submitted(system, invoice)
    worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
    worker.run_once()
    monkeypatch.setenv("CBIN_REQUIRE_FISCAL_VERIFICATION", "true")
    body = {
        "supplier_reference": "SUP-01",
        "account_reference": "INV-GOODS",
        "sku_mapping": {"SKU-1": "ITEM-CABLE"},
        "tax_mapping": {"15": "TAX-15"},
    }
    assert (
        client.post(
            f"/v1/documents/{ident}/accept", json=body, headers=headers(keys, "buyer:admin")
        ).status_code
        == 422
    )
    configured(monkeypatch, "FISCAL")

    def handler(request):
        expected = json.loads(request.content)["expected"]
        return httpx.Response(
            200,
            json={
                "status": "valid",
                "evidence_reference": "TEST-ONLY-F1",
                "matched_invoice": expected,
            },
        )

    with (
        app.state.sessions.begin() as db,
        httpx.Client(transport=httpx.MockTransport(handler)) as provider,
    ):
        actor = db.scalar(
            select(Credential).where(Credential.business_id == "buyer", Credential.role == "admin")
        )
        assert verify(db, actor, db.get(Document, ident), provider)["status"] == "valid"
    assert (
        client.post(
            f"/v1/documents/{ident}/accept", json=body, headers=headers(keys, "buyer:admin")
        ).status_code
        == 200
    )
    with (
        app.state.sessions.begin() as db,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"status": "valid", "matched_invoice": {}})
            )
        ) as provider,
    ):
        actor = db.scalar(
            select(Credential).where(Credential.business_id == "buyer", Credential.role == "admin")
        )
        with pytest.raises(DomainError) as err:
            verify(db, actor, db.get(Document, ident), provider)
        assert err.value.code == "FISCAL_RESULT_UNCONFIRMED"


def test_unconfigured_fiscal_and_ocr_do_not_fake_results(system, invoice, monkeypatch):
    _, client, keys, _ = system
    monkeypatch.delenv("CBIN_OCR_CONFIG", raising=False)
    assert (
        client.post(
            "/v1/receipt-captures", json=original(invoice), headers=headers(keys, "buyer:admin")
        ).status_code
        == 503
    )
    ident = submitted(system, invoice)
    assert (
        client.post(
            f"/v1/documents/{ident}/fiscal/verify", json={}, headers=headers(keys, "buyer:admin")
        ).status_code
        == 422
    )


def test_scan_intake_requires_review_is_buyer_scoped_and_keeps_provenance(
    system, invoice, monkeypatch
):
    app, client, keys, settings = system
    configured(monkeypatch, "OCR")
    with (
        app.state.sessions.begin() as db,
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"invoice": invoice}))
        ) as provider,
    ):
        actor = db.scalar(
            select(Credential).where(Credential.business_id == "buyer", Credential.role == "admin")
        )
        row = extract(db, actor, AttachmentUpload.model_validate(original(invoice)), provider)
        capture_id = row.id
    assert client.get("/v1/documents", headers=headers(keys, "buyer:admin")).json()["items"] == []
    path = f"/v1/receipt-captures/{capture_id}/confirm"
    assert (
        client.post(
            path, json={"reviewed": False}, headers=headers(keys, "buyer:admin")
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={"reviewed": True}, headers=headers(keys, "stranger:admin")
        ).status_code
        == 404
    )
    response = client.post(path, json={"reviewed": True}, headers=headers(keys, "buyer:admin"))
    assert response.status_code == 202, response.text
    ident = response.json()["id"]
    assert (
        client.get(f"/v1/documents/{ident}", headers=headers(keys, "buyer:admin")).json()[
            "provenance"
        ]
        == "buyer_scan"
    )
    again = client.post(path, json={"reviewed": True}, headers=headers(keys, "buyer:admin"))
    assert again.json()["id"] == ident
    Worker(settings, app.state.sessions, lambda *_: SandboxAdapter()).run_once()
    assert (
        client.get(f"/v1/documents/{ident}", headers=headers(keys, "buyer:admin")).json()["status"]
        == "delivered"
    )


def test_zoho_attachment_confirms_exact_bytes_and_never_overwrites_different_file(
    system, invoice, monkeypatch
):
    app, _, _, _ = system
    ident = submitted(system, invoice)
    with app.state.sessions.begin() as db:
        packet = evidence_packet(db, db.get(Document, ident))
    monkeypatch.setenv("ZOHO_TOKEN_TEST", "disposable")
    saved = []

    def handler(request):
        if request.method == "POST":
            assert b"Content-Disposition: form-data" in request.content
            saved.append(packet.content)
            return httpx.Response(201, json={"code": 0})
        return httpx.Response(200, content=saved[0]) if saved else httpx.Response(404)

    config = {"token_env": "ZOHO_TOKEN_TEST", "organization_id": "test-only"}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = ZohoBooksAdapter(config, client)
        assert adapter.attach_document("1", ident, packet).startswith("zoho-file:")
        assert adapter.attach_document("1", ident, packet, create_allowed=False).startswith(
            "zoho-file:"
        )
        assert len(saved) == 1
        saved[0] = b"%PDF-other"
        with pytest.raises(AmbiguousOutcome, match="MISMATCH"):
            adapter.attach_document("1", ident, packet)


def test_odoo_attachment_recovery_matches_native_checksum(system, invoice, monkeypatch):
    from test_odoo import CONFIG

    app, _, _, _ = system
    ident = submitted(system, invoice)
    with app.state.sessions() as db:
        file = db.scalar(
            select(InvoiceFile).where(
                InvoiceFile.document_id == ident, InvoiceFile.kind == "seller_original"
            )
        )
    monkeypatch.setenv("CBIN_ERP_ALLOWLIST", '["http://odoo-test:8069"]')
    monkeypatch.setenv("ODOO_TEST_PASSWORD", "disposable")
    saved = []

    def handler(request):
        body = json.loads(request.content)
        if body["params"]["service"] == "common":
            return httpx.Response(200, json={"result": 7})
        model, method, args, _ = body["params"]["args"][3:]
        assert model == "ir.attachment"
        if method == "create":
            saved.append(base64.b64decode(args[0]["datas"]))
            return httpx.Response(200, json={"result": 42})
        return httpx.Response(
            200,
            json={
                "result": [{"id": 42, "checksum": hashlib.sha1(saved[0]).hexdigest()}]
                if saved
                else []
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = OdooAdapter(CONFIG, client)
        assert adapter.attach_document("99", ident, file) == "42"
        assert adapter.attach_document("99", ident, file, create_allowed=False) == "42"
        assert len(saved) == 1


def test_leap_day_retention_is_at_least_six_calendar_years():
    from datetime import datetime, timezone

    start = int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp())
    assert (
        datetime.fromtimestamp(retain_until(start), timezone.utc)
        .isoformat()
        .startswith("2030-03-01")
    )


def test_empty_compose_provider_settings_remain_disabled(system, invoice, monkeypatch):
    _, client, keys, _ = system
    monkeypatch.setenv("CBIN_OCR_CONFIG", "")
    monkeypatch.setenv("CBIN_FISCAL_VERIFIER_CONFIG", "")
    assert (
        client.post(
            "/v1/receipt-captures", json=original(invoice), headers=headers(keys, "buyer:admin")
        ).status_code
        == 503
    )
    invoice["fiscal_metadata"] = {
        "device_serial": "D1",
        "zimra_signature": "unverified",
        "receipt_reference": "F1",
    }
    ident = submitted(system, invoice)
    assert (
        client.post(
            f"/v1/documents/{ident}/fiscal/verify", json={}, headers=headers(keys, "buyer:admin")
        ).status_code
        == 503
    )


def test_attachment_connection_change_never_sends_originals_to_another_erp(
    system, invoice, monkeypatch
):
    app, client, keys, settings = system
    ident = submitted(system, invoice)
    worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
    worker.run_once()
    body = {
        "supplier_reference": "SUP-01",
        "account_reference": "INV-GOODS",
        "sku_mapping": {"SKU-1": "ITEM-CABLE"},
        "tax_mapping": {"15": "TAX-15"},
    }
    assert (
        client.post(
            f"/v1/documents/{ident}/accept", json=body, headers=headers(keys, "buyer:admin")
        ).status_code
        == 200
    )
    worker.run_once()
    monkeypatch.setenv("CBIN_CONNECTOR_CONFIG", '{"buyer":{"type":"different-erp"}}')
    worker.run_once()
    with app.state.sessions() as db:
        failed = db.scalar(select(Job).where(Job.kind == "attachment", Job.last_error.is_not(None)))
        assert failed.last_error == "ATTACHMENT_CONNECTION_CHANGED"
        assert db.scalar(select(BillAttachment)) is None
        assert db.get(Document, ident).posted_reference == f"sandbox:{ident}"


def test_legacy_invoice_hash_stays_stable_without_optional_display_fields(system, invoice):
    from cbin.service import digest

    app, _, _, _ = system
    ident = submitted(system, invoice)
    old = invoice | {
        "schema_version": "1.0",
        "exchange_rate_zig": None,
        "fiscal_metadata": None,
        "correction_of": None,
    }
    with app.state.sessions() as db:
        assert db.get(Document, ident).request_hash == digest(old)
