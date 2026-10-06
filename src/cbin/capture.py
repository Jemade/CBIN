"""Paper intake via a configured extractor, with explicit human review and provenance."""

import hashlib
import json
import os
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from cbin.db import Business, ReceiptCapture
from cbin.documents import decode_upload, retain_until, save_file
from cbin.schemas import Invoice
from cbin.service import DomainError, now, record, submit, uid


def buyer_access(credential):
    if credential.role not in {"reviewer", "admin"}:
        raise DomainError("FORBIDDEN", "Buyer reviewer or administrator required", 403)


def serialize(row):
    return {
        "id": row.id,
        "filename": row.filename,
        "sha256": row.sha256,
        "candidate": row.candidate,
        "provider": row.provider,
        "created_at": row.created_at,
        "retain_until": row.retain_until,
        "document_id": row.document_id,
        "source": "buyer_scan",
        "review_required": not bool(row.document_id),
    }


def extract(db, credential, upload, client=None):
    buyer_access(credential)
    content = decode_upload(upload)
    entry = json.loads(os.getenv("CBIN_OCR_CONFIG") or "{}").get(credential.environment)
    if not entry or not entry.get("reviewed") or not entry.get("evidence_reference"):
        raise DomainError(
            "OCR_NOT_CONFIGURED",
            "Configure and test an extraction provider before scanning invoices",
            503,
        )
    url = entry.get("url", "")
    parsed = urlsplit(url)
    if (
        url not in json.loads(os.getenv("CBIN_OCR_ALLOWLIST", "[]"))
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise DomainError(
            "OCR_ENDPOINT_NOT_APPROVED", "The extraction endpoint is not approved", 503
        )
    token = os.getenv(entry.get("token_env", ""), "")
    if not token:
        raise DomainError(
            "OCR_CREDENTIAL_MISSING", "The extraction credential is not configured", 503
        )
    try:

        def read(active):
            response = active.post(
                url,
                json={
                    "file": upload.model_dump(),
                    "buyer_id": credential.business_id,
                    "environment": credential.environment,
                },
                headers={"Authorization": "Bearer " + token},
            )
            if response.status_code != 200 or len(response.content) > 1_000_000:
                raise ValueError
            return Invoice.model_validate(response.json()["invoice"])

        if client:
            invoice = read(client)
        else:
            with httpx.Client(timeout=20, follow_redirects=False) as active:
                invoice = read(active)
    except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError):
        raise DomainError(
            "OCR_UNCONFIRMED",
            "Extraction is incomplete or inconsistent; review the original invoice",
            422,
        ) from None
    if invoice.buyer.cbin_id != credential.business_id:
        raise DomainError("BUYER_MISMATCH", "The extracted invoice belongs to another buyer", 422)
    for party in (invoice.seller, invoice.buyer):
        business = db.get(Business, party.cbin_id)
        if (
            not business
            or business.environment != credential.environment
            or not business.verified
            or business.tin != party.tin
        ):
            raise DomainError(
                "IDENTITY_UNVERIFIED",
                "Match the extracted invoice to verified business identities",
                422,
            )
    created = now()
    row = ReceiptCapture(
        id=uid(),
        buyer_id=credential.business_id,
        environment=credential.environment,
        filename=upload.filename,
        media_type=upload.media_type,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        candidate=invoice.model_dump(mode="json"),
        provider=entry["provider"],
        created_at=created,
        retain_until=retain_until(created),
    )
    db.add(row)
    record(
        db,
        credential,
        "receipt.extracted",
        data={"capture_id": row.id, "sha256": row.sha256, "provider": row.provider},
    )
    return row


def confirm(db, credential, row):
    buyer_access(credential)
    if row.buyer_id != credential.business_id or row.environment != credential.environment:
        raise DomainError("CAPTURE_NOT_FOUND", "Receipt capture not found", 404)
    invoice = Invoice.model_validate(row.candidate)
    document, duplicate = submit(
        db, credential, invoice, "receipt-capture:" + row.id, buyer_capture=True
    )
    if not duplicate or document.status in {"queued", "delivered", "under_review"}:
        save_file(db, document, "buyer_scan", row.filename, row.media_type, row.content)
    row.document_id = document.id
    record(
        db,
        credential,
        "receipt.review_confirmed",
        document,
        {"capture_id": row.id, "sha256": row.sha256, "duplicate_transaction": duplicate},
    )
    return document, duplicate
