import base64
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from cbin.config import Settings
from cbin.connectors.catalogue import catalogue, software_by_id
from cbin.connectors.mapping import MappedSubmission, MappingError, MappingProfile, normalize
from cbin.db import (
    Attempt,
    AuditEvent,
    Business,
    Credential,
    Document,
    Job,
    WebhookEndpoint,
    make_database,
)
from cbin.schemas import (
    Accept,
    AttachmentUpload,
    CaptureConfirmation,
    Invoice,
    InvoicePackage,
    Reject,
    Replay,
    WebhookRegistration,
)
from cbin.security import key_digest
from cbin.service import (
    DomainError,
    decide,
    get_document,
    record,
    serialize_document,
    submit,
    uid,
    visible,
)

logger = logging.getLogger("cbin")


def encode_cursor(value):
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def decode_cursor(value):
    if value is None:
        return 0
    try:
        number = int(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if number < 0:
            raise ValueError
        return number
    except (ValueError, UnicodeError):
        raise DomainError("INVALID_CURSOR", "Invalid pagination cursor", 422) from None


def create_app(settings=None):
    settings = settings or Settings.from_env()
    engine, sessions = make_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="CBIN", version="0.1.0", lifespan=lifespan)
    app.state.engine, app.state.sessions, app.state.settings = engine, sessions, settings
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            incoming if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", incoming) else uid()
        )
        # Enforce the same 2 MB boundary for chunked and Content-Length requests.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2_000_000:
                return error_response(
                    request, "PAYLOAD_TOO_LARGE", "Maximum body size is 2 MB", 413
                )
        request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    def error_response(request, code, message, status, retryable=False):
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "request_id": getattr(request.state, "request_id", str(uuid4())),
                }
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return error_response(request, exc.code, exc.message, exc.status, exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo rejected payloads or secrets.
        fields = ", ".join(".".join(map(str, err["loc"])) for err in exc.errors()[:10])
        return error_response(request, "VALIDATION_ERROR", f"Invalid fields: {fields}", 422)

    def session():
        with sessions() as db:
            try:
                yield db
                db.commit()
            except IntegrityError:
                db.rollback()
                raise DomainError(
                    "CONCURRENT_WRITE",
                    "Concurrent update; retry with the same idempotency key",
                    409,
                    True,
                ) from None
            except Exception:
                db.rollback()
                raise

    def authenticate(
        auth: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(session)
    ):
        if auth is None:
            raise DomainError("UNAUTHORIZED", "Bearer credential required", 401)
        credential = db.scalar(
            select(Credential).where(
                Credential.digest == key_digest(auth.credentials, settings.pepper),
                Credential.environment == settings.environment,
                Credential.revoked.is_(False),
            )
        )
        if credential is None:
            raise DomainError("UNAUTHORIZED", "Invalid or revoked credential", 401)
        return credential

    def operator(credential=Depends(authenticate)):
        if credential.role != "operator":
            raise DomainError("FORBIDDEN", "Operator credential required", 403)
        return credential

    @app.get("/health/live", include_in_schema=False)
    def live():
        return {"status": "ok", "environment": settings.environment}

    @app.get("/health/ready", include_in_schema=False)
    def ready(db=Depends(session)):
        db.scalar(select(Credential.id).limit(1))
        return {"status": "ready"}

    @app.post("/v1/documents", status_code=202)
    def create_document(
        invoice: Invoice,
        idempotency_key: str = Header(min_length=1, max_length=200),
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        document, duplicate = submit(db, credential, invoice, idempotency_key)
        return {**serialize_document(document), "duplicate": duplicate}

    @app.get("/v1/me")
    def current_workspace(credential=Depends(authenticate), db=Depends(session)):
        business = db.get(Business, credential.business_id)
        return {
            "business": {"id": business.id, "name": business.name, "tin": business.tin},
            "role": credential.role,
            "environment": credential.environment,
        }

    @app.post("/v1/seller/invoice-packages", status_code=202)
    def send_invoice_package(
        body: InvoicePackage,
        idempotency_key: str = Header(min_length=1, max_length=200),
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        import hashlib

        from cbin.documents import decode_upload, files_for, upload_original

        content_hash = hashlib.sha256(decode_upload(body.original)).hexdigest()
        document, duplicate = submit(db, credential, body.invoice, idempotency_key)
        originals = [f for f in files_for(db, document) if f.kind == "seller_original"]
        if duplicate:
            if not any(f.sha256 == content_hash for f in originals):
                raise DomainError(
                    "ORIGINAL_CONFLICT",
                    "This package was already sent with different invoice evidence",
                    409,
                )
        else:
            upload_original(db, credential, document, body.original)
        return {**serialize_document(document), "duplicate": duplicate}

    @app.get("/v1/seller/import-profiles")
    def seller_import_profiles(credential=Depends(authenticate)):
        if credential.role not in {"submitter", "admin"}:
            raise DomainError("FORBIDDEN", "Seller submitter or administrator required", 403)
        entries = (
            json.loads(os.getenv("CBIN_IMPORT_PROFILES") or "{}")
            .get(credential.environment, {})
            .get(credential.business_id, {})
        )
        return {
            "items": [
                {
                    "software_id": software,
                    "profile_id": ident,
                    "evidence_reference": profile.get("evidence_reference"),
                }
                for software, profiles in entries.items()
                for ident, profile in profiles.items()
                if profile.get("reviewed") and software_by_id(software)
            ]
        }

    @app.get("/v1/seller/source-documents")
    def seller_source_documents(credential=Depends(authenticate), db=Depends(session)):
        from cbin.connectors.base import ConnectorError
        from cbin.connectors.registry import adapter_for

        if credential.role not in {"submitter", "admin"}:
            raise DomainError("FORBIDDEN", "Seller submitter or administrator required", 403)
        adapter = None
        try:
            adapter = adapter_for(credential.business_id, credential.environment)
            if not hasattr(adapter, "source_documents"):
                raise ConnectorError("SOURCE_LOOKUP_NOT_IMPLEMENTED")
            items = adapter.source_documents()
            existing_by_reference = {
                row.external_reference: row
                for row in db.scalars(
                    select(Document).where(
                        Document.environment == credential.environment,
                        Document.seller_id == credential.business_id,
                        Document.external_reference.in_([item["reference"] for item in items]),
                    )
                )
            }
            for item in items:
                existing = existing_by_reference.get(item["reference"])
                item["exchange_id"] = existing.id if existing else None
                item["exchange_status"] = existing.status if existing else None
            return {"items": items}
        except ConnectorError as exc:
            raise DomainError("SOURCE_UNAVAILABLE", str(exc), 503, True) from None
        finally:
            if adapter and hasattr(adapter, "close"):
                adapter.close()

    @app.post("/v1/seller/source-documents/{source_id}/send", status_code=202)
    def send_source_document(source_id: str, credential=Depends(authenticate), db=Depends(session)):
        from cbin.connectors.base import ConnectorError
        from cbin.connectors.registry import adapter_for, connection_fingerprint

        if credential.role not in {"submitter", "admin"}:
            raise DomainError("FORBIDDEN", "Seller submitter or administrator required", 403)
        if not source_id.isdigit() or len(source_id) > 15:
            raise DomainError("INVALID_SOURCE_ID", "Select a source invoice", 422)
        adapter = None
        try:
            adapter = adapter_for(credential.business_id, credential.environment)
            if not hasattr(adapter, "source_invoice"):
                raise ConnectorError("SOURCE_LOOKUP_NOT_IMPLEMENTED")
            invoice = adapter.source_invoice(source_id)
            document, duplicate = submit(
                db,
                credential,
                invoice,
                "source:"
                + connection_fingerprint(credential.business_id, credential.environment)
                + ":"
                + source_id,
            )
            if not duplicate and hasattr(adapter, "source_attachment"):
                from cbin.documents import upload_original

                original = adapter.source_attachment(source_id)
                if original is not None:
                    upload_original(db, credential, document, original)
            return {**serialize_document(document), "duplicate": duplicate}
        except ConnectorError as exc:
            raise DomainError("SOURCE_UNAVAILABLE", str(exc), 503, True) from None
        finally:
            if adapter and hasattr(adapter, "close"):
                adapter.close()

    @app.get("/v1/connectors")
    def list_connectors(credential=Depends(authenticate)):
        return catalogue()

    @app.post("/v1/connectors/{software_id}/documents", status_code=202)
    def import_document(
        software_id: str,
        body: MappedSubmission,
        idempotency_key: str = Header(min_length=1, max_length=200),
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        if credential.role not in {"submitter", "admin"}:
            raise DomainError("FORBIDDEN", "Submitter or business admin credential required", 403)
        if software_by_id(software_id) is None:
            raise DomainError("SOFTWARE_NOT_FOUND", "Software not in the observed catalogue", 404)
        try:
            profiles = json.loads(os.environ.get("CBIN_IMPORT_PROFILES") or "{}")
            entry = profiles
            for scope in (
                credential.environment,
                credential.business_id,
                software_id,
                body.profile_id,
            ):
                if not isinstance(entry, dict):
                    raise ValueError("Invalid scope configuration")
                entry = entry.get(scope, {})
            entry = entry or None
            if entry is None:
                raise DomainError(
                    "IMPORT_PROFILE_NOT_CONFIGURED",
                    "Operator-reviewed mapping profile required",
                    409,
                )
            profile = MappingProfile.model_validate(entry)
        except (ValueError, TypeError):
            raise DomainError(
                "IMPORT_CONFIGURATION_INVALID", "Operator must correct import configuration", 503
            ) from None
        try:
            invoice = normalize(body.vendor_payload, profile)
        except MappingError as exc:
            raise DomainError(str(exc), "Vendor payload could not be mapped safely", 422) from None
        document, duplicate = submit(db, credential, invoice, idempotency_key)
        if not duplicate:
            record(
                db,
                credential,
                "connector.imported",
                document,
                {
                    "software_id": software_id,
                    "profile_id": body.profile_id,
                    "evidence_reference": profile.evidence_reference,
                },
            )
        return {**serialize_document(document), "duplicate": duplicate, "software_id": software_id}

    @app.get("/v1/documents")
    def list_documents(
        external_reference: str | None = Query(None, max_length=120),
        status: str | None = Query(None, max_length=30),
        direction: str | None = Query(None, pattern="^(incoming|outgoing)$"),
        offset: int = Query(0, ge=0, le=100000),
        limit: int = Query(50, ge=1, le=100),
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        query = select(Document).where(*visible(credential))
        if direction:
            query = query.where(
                (Document.buyer_id if direction == "incoming" else Document.seller_id)
                == credential.business_id
            )
        if external_reference:
            query = query.where(Document.external_reference == external_reference)
        if status:
            query = query.where(Document.status == status)
        docs = db.scalars(
            query.order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit)
        ).all()
        identities = {ident for doc in docs for ident in (doc.seller_id, doc.buyer_id)}
        names = {
            row.id: row.name
            for row in db.scalars(
                select(Business).where(
                    Business.environment == credential.environment, Business.id.in_(identities)
                )
            )
        }
        return {
            "items": [
                {
                    **serialize_document(d),
                    "seller_name": names.get(d.seller_id),
                    "buyer_name": names.get(d.buyer_id),
                }
                for d in docs
            ],
            "offset": offset,
            "limit": limit,
        }

    @app.post("/v1/receipt-captures", status_code=201)
    def extract_receipt(
        body: AttachmentUpload, credential=Depends(authenticate), db=Depends(session)
    ):
        from cbin.capture import extract, serialize

        return serialize(extract(db, credential, body))

    @app.get("/v1/receipt-captures")
    def receipt_captures(credential=Depends(authenticate), db=Depends(session)):
        from cbin.capture import buyer_access, serialize
        from cbin.db import ReceiptCapture

        buyer_access(credential)
        rows = db.scalars(
            select(ReceiptCapture)
            .where(
                ReceiptCapture.environment == credential.environment,
                ReceiptCapture.buyer_id == credential.business_id,
            )
            .order_by(ReceiptCapture.created_at.desc())
            .limit(50)
        )
        return {"items": [serialize(row) for row in rows]}

    @app.post("/v1/receipt-captures/{capture_id}/confirm", status_code=202)
    def confirm_receipt(
        capture_id: str,
        body: CaptureConfirmation,
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        from cbin.capture import confirm
        from cbin.db import ReceiptCapture

        row = db.scalar(
            select(ReceiptCapture)
            .where(
                ReceiptCapture.id == capture_id,
                ReceiptCapture.environment == credential.environment,
                ReceiptCapture.buyer_id == credential.business_id,
            )
            .with_for_update()
        )
        if not row:
            raise DomainError("CAPTURE_NOT_FOUND", "Receipt capture not found", 404)
        document, duplicate = confirm(db, credential, row)
        return {**serialize_document(document), "duplicate": duplicate}

    @app.get("/v1/receipt-captures/{capture_id}/original")
    def capture_original(capture_id: str, credential=Depends(authenticate), db=Depends(session)):
        from cbin.db import ReceiptCapture

        row = db.get(ReceiptCapture, capture_id)
        if (
            not row
            or row.buyer_id != credential.business_id
            or row.environment != credential.environment
        ):
            raise DomainError("CAPTURE_NOT_FOUND", "Receipt capture not found", 404)
        return Response(
            row.content,
            media_type=row.media_type,
            headers={
                "Content-Disposition": 'attachment; filename="' + row.filename + '"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/v1/documents/{document_id}")
    def read_document(document_id: str, credential=Depends(authenticate), db=Depends(session)):
        from cbin.db import BillAttachment
        from cbin.documents import file_summary, files_for
        from cbin.fiscal import current_check

        document = get_document(db, credential, document_id)
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.document_id == document.id).order_by(AuditEvent.id)
        ).all()
        linked = {
            row.file_id: row.provider_reference
            for row in db.scalars(
                select(BillAttachment).where(BillAttachment.document_id == document.id)
            )
        }
        attachment_jobs = {
            row.payload["file_id"]: row.state
            for row in db.scalars(
                select(Job).where(Job.document_id == document.id, Job.kind == "attachment")
            )
        }
        return {
            **serialize_document(document),
            "files": [
                {
                    **file_summary(row),
                    "erp_status": "stored"
                    if row.id in linked
                    else attachment_jobs.get(row.id, "retained_in_cbin"),
                    "erp_reference": linked.get(row.id),
                }
                for row in files_for(db, document)
            ],
            "fiscal_check": current_check(db, document),
            "fiscal_verification": current_check(db, document)["status"],
            "provenance": "buyer_scan"
            if any(e.kind == "invoice.buyer_capture" for e in events)
            else "seller_submission",
            "seller_name": db.get(Business, document.seller_id).name,
            "buyer_name": db.get(Business, document.buyer_id).name,
            "timeline": [
                {"id": e.id, "kind": e.kind, "created_at": e.created_at, "data": e.data}
                for e in events
            ],
        }

    @app.post("/v1/documents/{document_id}/files", status_code=201)
    def add_original(
        document_id: str,
        body: AttachmentUpload,
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        from cbin.documents import file_summary, upload_original

        document = get_document(db, credential, document_id)
        # Share the document lock with buyer decisions to seal evidence atomically.
        db.scalar(select(Document).where(Document.id == document.id).with_for_update())
        db.refresh(document)
        return file_summary(upload_original(db, credential, document, body))

    @app.get("/v1/documents/{document_id}/files/{file_id}")
    def download_original(
        document_id: str, file_id: str, credential=Depends(authenticate), db=Depends(session)
    ):
        from cbin.db import InvoiceFile

        document = get_document(db, credential, document_id)
        row = db.get(InvoiceFile, file_id)
        if not row or row.document_id != document.id or row.environment != credential.environment:
            raise DomainError("FILE_NOT_FOUND", "File not found", 404)
        record(
            db,
            credential,
            "invoice.evidence_downloaded",
            document,
            {"file_id": row.id},
            notify=False,
        )
        return Response(
            row.content,
            media_type=row.media_type,
            headers={
                "Content-Disposition": 'attachment; filename="' + row.filename + '"',
                "Cache-Control": "no-store",
                "X-Content-SHA256": row.sha256,
            },
        )

    @app.get("/v1/documents/{document_id}/print")
    def print_copy(
        document_id: str,
        layout: str = Query("a4", pattern="^(a4|receipt48|escpos32|escpos48)$"),
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        from cbin.documents import escpos, invoice_pdf

        document = get_document(db, credential, document_id)
        raw = layout.startswith("escpos")
        content = (
            escpos(document.payload, int(layout[-2:]))
            if raw
            else invoice_pdf(document.payload, layout)
        )
        record(
            db,
            credential,
            "invoice.print_copy_downloaded",
            document,
            {"layout": layout},
            notify=False,
        )
        return Response(
            content,
            media_type="application/octet-stream" if raw else "application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="invoice-'
                + layout
                + ('.bin"' if raw else '.pdf"'),
                "Cache-Control": "no-store",
            },
        )

    @app.post("/v1/documents/{document_id}/fiscal/verify")
    def check_fiscal(document_id: str, credential=Depends(authenticate), db=Depends(session)):
        from cbin.fiscal import verify

        return verify(db, credential, get_document(db, credential, document_id))

    @app.get("/v1/documents/{document_id}/bookkeeping")
    def bookkeeping_context(
        document_id: str, credential=Depends(authenticate), db=Depends(session)
    ):
        from cbin.bookkeeping import buyer_access, references, suggestions

        document = get_document(db, credential, document_id)
        buyer_access(credential, document)
        catalogue = references(db, credential)
        return {
            "references": catalogue,
            "suggestions": suggestions(db, credential, document, catalogue),
        }

    @app.post("/v1/documents/{document_id}/bookkeeping/preview")
    def bookkeeping_preview(
        document_id: str, body: Accept, credential=Depends(authenticate), db=Depends(session)
    ):
        from cbin.bookkeeping import preview

        return preview(db, credential, get_document(db, credential, document_id), body)

    @app.post("/v1/accounting/references/refresh")
    def refresh_accounting(credential=Depends(authenticate), db=Depends(session)):
        from cbin.bookkeeping import references

        if credential.role not in {"reviewer", "admin"}:
            raise DomainError("FORBIDDEN", "Buyer reviewer or administrator required", 403)
        return references(db, credential, refresh=True)

    @app.post("/v1/documents/{document_id}/accept")
    def accept_document(
        document_id: str, body: Accept, credential=Depends(authenticate), db=Depends(session)
    ):
        return serialize_document(decide(db, credential, document_id, "accept", body))

    @app.post("/v1/documents/{document_id}/reject")
    def reject_document(
        document_id: str, body: Reject, credential=Depends(authenticate), db=Depends(session)
    ):
        return serialize_document(decide(db, credential, document_id, "reject", body))

    @app.get("/v1/businesses/{cbin_id}")
    def business_identity(cbin_id: str, credential=Depends(authenticate), db=Depends(session)):
        business = db.get(Business, cbin_id)
        if not business or business.environment != credential.environment or not business.verified:
            raise DomainError("BUSINESS_NOT_FOUND", "Verified business not found", 404)
        return {
            "cbin_id": business.id,
            "name": business.name,
            "tin": business.tin,
            "registration": business.registration,
            "verified": business.verified,
        }

    @app.post("/v1/webhook-endpoints", status_code=201)
    def add_webhook(
        body: WebhookRegistration, credential=Depends(authenticate), db=Depends(session)
    ):
        from urllib.parse import urlsplit

        parsed = urlsplit(body.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise DomainError(
                "INVALID_WEBHOOK_URL", "Use an HTTPS URL without credentials or fragments", 422
            )
        if credential.role != "admin":
            raise DomainError("FORBIDDEN", "Business admin credential required", 403)
        endpoint = WebhookEndpoint(
            id=uid(),
            environment=credential.environment,
            business_id=credential.business_id,
            url=body.url,
            secret_ref=body.secret_ref,
        )
        db.add(endpoint)
        record(db, credential, "webhook.registered", data={"endpoint_id": endpoint.id})
        return {
            "id": endpoint.id,
            "url": endpoint.url,
            "status": "requires_operator_egress_approval",
        }

    @app.get("/v1/events")
    def list_events(
        cursor: str | None = Query(None, max_length=100),
        limit: int = Query(50, ge=1, le=100),
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        query = select(AuditEvent).where(
            AuditEvent.environment == credential.environment, AuditEvent.id > decode_cursor(cursor)
        )
        if credential.role != "operator":
            allowed = select(Document.id).where(*visible(credential))
            query = query.where(or_event_access(credential, allowed))
        rows = db.scalars(query.order_by(AuditEvent.id).limit(limit + 1)).all()
        items = rows[:limit]
        return {
            "items": [
                {
                    "id": e.id,
                    "document_id": e.document_id,
                    "kind": e.kind,
                    "created_at": e.created_at,
                    "data": e.data,
                }
                for e in items
            ],
            "next_cursor": encode_cursor(items[-1].id) if len(rows) > limit else None,
        }

    @app.get("/v1/operations/jobs")
    def jobs(
        credential=Depends(operator), db=Depends(session), limit: int = Query(50, ge=1, le=100)
    ):
        rows = db.scalars(
            select(Job)
            .where(Job.environment == credential.environment)
            .order_by(Job.available_at)
            .limit(limit)
        ).all()
        return {
            "items": [
                {
                    "id": j.id,
                    "kind": j.kind,
                    "state": j.state,
                    "attempts": j.attempts,
                    "last_error": j.last_error,
                    "document_id": j.document_id,
                }
                for j in rows
            ]
        }

    @app.get("/v1/operations/metrics")
    def metrics(credential=Depends(operator), db=Depends(session)):
        counts = db.execute(
            select(Job.state, func.count())
            .where(Job.environment == credential.environment)
            .group_by(Job.state)
        ).all()
        return {"jobs": dict(counts)}

    @app.get("/v1/operations/jobs/{job_id}/attempts")
    def attempts(job_id: str, credential=Depends(operator), db=Depends(session)):
        job = db.get(Job, job_id)
        if not job or job.environment != credential.environment:
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        rows = db.scalars(
            select(Attempt).where(Attempt.job_id == job_id).order_by(Attempt.started_at)
        ).all()
        return {
            "items": [
                {
                    "id": a.id,
                    "outcome": a.outcome,
                    "started_at": a.started_at,
                    "finished_at": a.finished_at,
                }
                for a in rows
            ]
        }

    @app.post("/v1/operations/jobs/{job_id}/reconcile")
    def reconcile(job_id: str, body: Replay, credential=Depends(operator), db=Depends(session)):
        job = db.get(Job, job_id)
        if not job or job.environment != credential.environment:
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        if job.state != "needs_reconciliation" or job.kind not in {"post", "attachment"}:
            raise DomainError("INVALID_STATE", "Only ambiguous posting jobs can be reconciled")
        job.state, job.available_at = "pending", 0
        document = db.get(Document, job.document_id)
        record(
            db,
            credential,
            "posting.reconciliation_requested",
            document,
            {"job_id": job.id, "reason": body.reason},
        )
        return {"id": job.id, "state": "pending", "mode": "lookup_only"}

    @app.post("/v1/operations/jobs/{job_id}/replay")
    def replay(job_id: str, body: Replay, credential=Depends(operator), db=Depends(session)):
        job = db.get(Job, job_id)
        if not job or job.environment != credential.environment:
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        if job.state != "dead_letter":
            raise DomainError("INVALID_STATE", "Only dead-letter jobs can be replayed")
        job.state, job.attempts, job.available_at, job.last_error = "pending", 0, 0, None
        document = db.get(Document, job.document_id) if job.document_id else None
        record(db, credential, "job.replayed", document, {"job_id": job.id, "reason": body.reason})
        return {"id": job.id, "state": job.state}

    portal = Path(__file__).resolve().parent / "portal"

    @app.get("/", include_in_schema=False)
    def portal_page():
        return FileResponse(portal / "index.html")

    app.mount("/assets", StaticFiles(directory=portal / "assets", check_dir=False), name="assets")

    return app


def or_event_access(credential, allowed):
    from sqlalchemy import or_

    return or_(
        AuditEvent.document_id.in_(allowed),
        (AuditEvent.document_id.is_(None)) & (AuditEvent.business_id == credential.business_id),
    )
