import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import or_, select, update

from cbin.db import AuditEvent, Business, Decision, Document, Idempotency, Job, WebhookEndpoint


def uid():
    return str(uuid4())


def now():
    return int(time.time())


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class DomainError(Exception):
    def __init__(self, code, message, status=409, retryable=False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


def fail(code, message, status=409):
    raise DomainError(code, message, status)


def enqueue(db, environment, kind, payload, unique_key, document_id=None):
    job = Job(
        id=uid(),
        unique_key=unique_key,
        environment=environment,
        document_id=document_id,
        kind=kind,
        payload=payload,
        available_at=now(),
    )
    db.add(job)
    return job


def record(db, credential, kind, document=None, data=None):
    event = AuditEvent(
        environment=credential.environment,
        document_id=document.id if document else None,
        business_id=credential.business_id,
        actor=credential.id,
        kind=kind,
        data=data or {},
        created_at=now(),
    )
    db.add(event)
    db.flush()
    if document:
        endpoints = db.scalars(
            select(WebhookEndpoint).where(
                WebhookEndpoint.environment == credential.environment,
                WebhookEndpoint.business_id.in_([document.seller_id, document.buyer_id]),
                WebhookEndpoint.enabled.is_(True),
            )
        ).all()
        for endpoint in endpoints:
            enqueue(
                db,
                credential.environment,
                "webhook",
                {
                    "endpoint_id": endpoint.id,
                    "event": {
                        "id": event.id,
                        "type": kind,
                        "document_id": document.id,
                        "status": document.status,
                        "created_at": event.created_at,
                    },
                },
                f"event:{event.id}:endpoint:{endpoint.id}",
                document.id,
            )
    return event


def visible(credential):
    filters = [Document.environment == credential.environment]
    if credential.role != "operator":
        filters.append(
            or_(
                Document.seller_id == credential.business_id,
                Document.buyer_id == credential.business_id,
            )
        )
    return filters


def get_document(db, credential, document_id):
    document = db.scalar(select(Document).where(Document.id == document_id, *visible(credential)))
    if document is None:
        fail("DOCUMENT_NOT_FOUND", "Document not found", 404)
    return document


def submit(db, credential, invoice, key):
    if credential.role not in {"submitter", "admin"}:
        fail("FORBIDDEN", "A submitter or business admin credential is required", 403)
    payload = invoice.model_dump(mode="json")
    request_hash = digest(payload)
    existing = db.scalar(
        select(Idempotency).where(
            Idempotency.environment == credential.environment,
            Idempotency.business_id == credential.business_id,
            Idempotency.key == key,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            fail("IDEMPOTENCY_CONFLICT", "The key was used for a different payload")
        return db.get(Document, existing.document_id), True
    if invoice.seller.cbin_id != credential.business_id:
        fail("SELLER_MISMATCH", "Credential does not belong to the seller", 403)
    for party in (invoice.seller, invoice.buyer):
        business = db.get(Business, party.cbin_id)
        if (
            business is None
            or business.environment != credential.environment
            or business.tin != party.tin
            or not business.verified
        ):
            fail("IDENTITY_UNVERIFIED", "Both parties need verified matching identities", 422)
    if invoice.correction_of:
        original = get_document(db, credential, invoice.correction_of)
        if (
            original.seller_id != invoice.seller.cbin_id
            or original.buyer_id != invoice.buyer.cbin_id
            or original.payload["currency"] != invoice.currency
            or original.payload["document_type"] != "B2B_INVOICE"
        ):
            fail(
                "INVALID_CORRECTION", "Correction must reference the same parties and currency", 422
            )
    # A source reference identifies a business transaction across all connectors.
    fingerprint = digest([invoice.document_type, invoice.external_reference])
    document = db.scalar(
        select(Document).where(
            Document.environment == credential.environment,
            Document.seller_id == credential.business_id,
            Document.fingerprint == fingerprint,
        )
    )
    duplicate = document is not None
    if document and document.request_hash != request_hash:
        fail("TRANSACTION_CONFLICT", "The source reference already has different immutable data")
    if not document:
        document = Document(
            id=uid(),
            environment=credential.environment,
            seller_id=invoice.seller.cbin_id,
            buyer_id=invoice.buyer.cbin_id,
            external_reference=invoice.external_reference,
            fingerprint=fingerprint,
            request_hash=request_hash,
            payload=payload,
            status="queued",
            created_at=now(),
        )
        db.add(document)
        db.flush()
        for kind in ("received", "validated", "queued"):
            record(db, credential, f"document.{kind}", document)
        enqueue(db, credential.environment, "route", {}, f"route:{document.id}", document.id)
    db.add(
        Idempotency(
            environment=credential.environment,
            business_id=credential.business_id,
            key=key,
            request_hash=request_hash,
            document_id=document.id,
        )
    )
    db.flush()
    return document, duplicate


def decide(db, credential, document_id, action, body):
    if credential.role not in {"reviewer", "admin"}:
        fail("FORBIDDEN", "A reviewer or business admin credential is required", 403)
    document = get_document(db, credential, document_id)
    if document.buyer_id != credential.business_id:
        fail("FORBIDDEN", "Only the buyer may decide", 403)
    old = db.get(Decision, document_id)
    mapping = body.model_dump(mode="json") if action == "accept" else {}
    reason = body.reason if action == "reject" else None
    if old:
        if (old.action, old.mapping, old.reason) != (action, mapping, reason):
            fail("DECISION_CONFLICT", "A different decision already exists")
        return document
    if document.status not in {"delivered", "under_review"}:
        fail("INVALID_STATE", "Document must be delivered before buyer review")
    if action == "accept":
        codes = {line["item_code"] for line in document.payload["line_items"]}
        if set(body.sku_mapping) != codes or not all(v.strip() for v in body.sku_mapping.values()):
            fail("MAPPING_INCOMPLETE", "Map every seller SKU to a buyer SKU", 422)
    target = "accepted" if action == "accept" else "rejected_by_buyer"
    changed = db.execute(
        update(Document)
        .where(Document.id == document.id, Document.status == document.status)
        .values(status=target)
    )
    if changed.rowcount != 1:
        fail("CONCURRENT_DECISION", "Another review changed this document; reload")
    document.status = target
    db.add(
        Decision(
            document_id=document.id,
            actor=credential.id,
            action=action,
            mapping=mapping,
            reason=reason,
        )
    )
    record(
        db,
        credential,
        f"document.{target}",
        document,
        {"reason": reason} if reason else {"mapping_complete": True},
    )
    if action == "accept":
        enqueue(db, credential.environment, "post", {}, f"post:{document.id}", document.id)
    return document


def serialize_document(document):
    return {
        "id": document.id,
        "status": document.status,
        "created_at": document.created_at,
        "posted_reference": document.posted_reference,
        "payload": document.payload,
        "fiscal_verification": "not_verified"
        if document.payload.get("fiscal_metadata")
        else "not_supplied",
    }
