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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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
from cbin.schemas import Accept, Invoice, Reject, Replay, WebhookRegistration
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
        offset: int = Query(0, ge=0, le=100000),
        limit: int = Query(50, ge=1, le=100),
        credential=Depends(authenticate),
        db=Depends(session),
    ):
        query = select(Document).where(*visible(credential))
        if external_reference:
            query = query.where(Document.external_reference == external_reference)
        if status:
            query = query.where(Document.status == status)
        docs = db.scalars(
            query.order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit)
        ).all()
        return {"items": [serialize_document(d) for d in docs], "offset": offset, "limit": limit}

    @app.get("/v1/documents/{document_id}")
    def read_document(document_id: str, credential=Depends(authenticate), db=Depends(session)):
        document = get_document(db, credential, document_id)
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.document_id == document.id).order_by(AuditEvent.id)
        ).all()
        return {
            **serialize_document(document),
            "timeline": [
                {"id": e.id, "kind": e.kind, "created_at": e.created_at, "data": e.data}
                for e in events
            ],
        }

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
        if job.state != "needs_reconciliation" or job.kind != "post":
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

    @app.get("/portal.js", include_in_schema=False)
    def portal_script():
        return FileResponse(portal / "portal.js", media_type="application/javascript")

    @app.get("/portal.css", include_in_schema=False)
    def portal_styles():
        return FileResponse(portal / "portal.css", media_type="text/css")

    return app


def or_event_access(credential, allowed):
    from sqlalchemy import or_

    return or_(
        AuditEvent.document_id.in_(allowed),
        (AuditEvent.document_id.is_(None)) & (AuditEvent.business_id == credential.business_id),
    )
