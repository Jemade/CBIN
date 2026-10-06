"""Contract for an operator-reviewed fiscal verification service, not a guessed FDMS API."""

import json
import os
import re
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from cbin.db import FiscalCheck
from cbin.service import DomainError, now, record, uid


def current_check(db, document):
    row = db.scalar(
        select(FiscalCheck)
        .where(
            FiscalCheck.document_id == document.id,
            FiscalCheck.environment == document.environment,
            FiscalCheck.invoice_hash == document.request_hash,
        )
        .order_by(FiscalCheck.sequence.desc())
    )
    if not row:
        return {
            "status": "not_verified" if document.payload.get("fiscal_metadata") else "not_supplied"
        }
    return {
        "status": row.status,
        "provider": row.provider,
        "evidence_reference": row.evidence_reference,
        "checked_at": row.checked_at,
        "invoice_hash": row.invoice_hash,
    }


def verify(db, credential, document, client=None):
    if credential.role not in {"reviewer", "admin", "operator"} or (
        credential.role != "operator" and credential.business_id != document.buyer_id
    ):
        raise DomainError("FORBIDDEN", "Buyer reviewer or operator required", 403)
    fiscal = document.payload.get("fiscal_metadata")
    if not fiscal or not fiscal.get("receipt_reference"):
        raise DomainError(
            "FISCAL_EVIDENCE_MISSING", "A supplier fiscal receipt reference is required", 422
        )
    config = json.loads(os.getenv("CBIN_FISCAL_VERIFIER_CONFIG") or "{}")
    entry = config.get(document.environment)
    if not entry or not entry.get("reviewed") or not entry.get("evidence_reference"):
        raise DomainError(
            "FISCAL_VERIFIER_NOT_CONFIGURED",
            "Configure a reviewed fiscal verification provider",
            503,
        )
    url = entry.get("url", "")
    parsed = urlsplit(url)
    allowed = json.loads(os.getenv("CBIN_FISCAL_VERIFIER_ALLOWLIST", "[]"))
    if (
        url not in allowed
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise DomainError(
            "FISCAL_ENDPOINT_NOT_APPROVED", "Fiscal verification endpoint is not approved", 503
        )
    token = os.getenv(entry.get("token_env", ""), "")
    if not token:
        raise DomainError(
            "FISCAL_CREDENTIAL_MISSING", "Fiscal provider credential is not configured", 503
        )
    expected = {
        "invoice_hash": document.request_hash,
        "external_reference": document.external_reference,
        "seller_tin": document.payload["seller"]["tin"],
        "buyer_tin": document.payload["buyer"]["tin"],
        "currency": document.payload["currency"],
        "grand_total_minor": document.payload["totals"]["grand_total_minor"],
        "receipt_reference": fiscal["receipt_reference"],
    }
    try:

        def read(active):
            response = active.post(
                url,
                json={
                    "document_id": document.id,
                    "environment": document.environment,
                    "invoice": document.payload,
                    "expected": expected,
                },
                headers={"Authorization": "Bearer " + token},
            )
            if response.status_code != 200 or len(response.content) > 20_000:
                raise ValueError
            return response.json()

        if client is None:
            with httpx.Client(timeout=20, follow_redirects=False) as active:
                result = read(active)
        else:
            result = read(client)
        reference = result.get("evidence_reference", "")
        if (
            result.get("status") not in {"valid", "invalid", "unavailable"}
            or not re.fullmatch(r"[a-zA-Z0-9._:/-]{1,200}", reference)
            or any(result.get("matched_invoice", {}).get(k) != v for k, v in expected.items())
        ):
            raise ValueError
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise DomainError(
            "FISCAL_RESULT_UNCONFIRMED",
            "The fiscal check could not be confirmed; do not treat it as valid",
            503,
            True,
        ) from None
    row = FiscalCheck(
        id=uid(),
        document_id=document.id,
        environment=document.environment,
        invoice_hash=document.request_hash,
        status=result["status"],
        provider=entry["provider"],
        evidence_reference=reference,
        checked_at=now(),
    )
    db.add(row)
    record(
        db,
        credential,
        "fiscal.checked",
        document,
        {
            "status": row.status,
            "provider": row.provider,
            "evidence_reference": row.evidence_reference,
        },
    )
    return current_check(db, document)
