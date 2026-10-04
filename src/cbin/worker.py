import json
import logging
import os
import random
import signal
import time

import httpx
from sqlalchemy import or_, select, update

from cbin.config import Settings
from cbin.connectors.base import AmbiguousOutcome, ConnectorError
from cbin.connectors.registry import adapter_for
from cbin.db import Attempt, Credential, Decision, Document, Job, WebhookEndpoint, make_database
from cbin.security import sign_event
from cbin.service import canonical_json, now, record, uid

logger = logging.getLogger("cbin.worker")


class Worker:
    def __init__(self, settings, sessions, adapter_factory=adapter_for):
        self.settings, self.sessions, self.adapter_factory = settings, sessions, adapter_factory

    def claim(self):
        with self.sessions.begin() as db:
            query = (
                select(Job)
                .where(
                    Job.environment == self.settings.environment,
                    or_(
                        (Job.state == "pending") & (Job.available_at <= now()),
                        (Job.state == "running") & (Job.lease_until < now()),
                    ),
                )
                .order_by(Job.available_at)
                .limit(1)
            )
            if db.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            job = db.scalar(query)
            if not job:
                return None
            token = uid()
            changed = db.execute(
                update(Job)
                .where(Job.id == job.id, Job.state == job.state, Job.lease_token == job.lease_token)
                .values(
                    state="running",
                    lease_token=token,
                    lease_until=now() + self.settings.lease_seconds,
                    attempts=job.attempts + 1,
                )
            )
            if changed.rowcount != 1:
                return None
            attempt = Attempt(id=uid(), job_id=job.id, started_at=now())
            db.add(attempt)
            db.flush()
            return job.id, token, attempt.id

    def run_once(self):
        claimed = self.claim()
        if not claimed:
            return False
        job_id, token, attempt_id = claimed
        with self.sessions() as db:
            job = db.get(Job, job_id)
            document = db.get(Document, job.document_id) if job.document_id else None
            previous = db.scalars(
                select(Attempt)
                .where(Attempt.job_id == job_id, Attempt.id != attempt_id)
                .order_by(Attempt.started_at.desc())
            ).all()
            # Recover a previous write before creating anything else, including after a crash.
            uncertain = any(a.outcome in {"started", "ambiguous"} for a in previous)
            decision = db.get(Decision, job.document_id) if job.kind == "post" else None
            endpoint = (
                db.get(WebhookEndpoint, job.payload["endpoint_id"])
                if job.kind == "webhook"
                else None
            )
        reference, code, outcome = None, None, "success"
        adapter = None
        try:
            if job.kind == "route":
                pass  # Portal delivery is the initial acknowledged recipient channel.
            elif job.kind == "post":
                adapter = self.adapter_factory(document.buyer_id, job.environment)
                reference = adapter.recover_from_timeout(
                    document.id, document.payload, decision.mapping
                )
                if reference is None:
                    if uncertain:
                        raise AmbiguousOutcome("POST_REQUIRES_RECONCILIATION")
                    reference = adapter.post_to_ledger(
                        document.id, document.payload, decision.mapping
                    )
            elif job.kind == "webhook":
                self.deliver_webhook(endpoint, job.payload["event"])
            else:
                raise ConnectorError("UNKNOWN_JOB_KIND")
        except AmbiguousOutcome as exc:
            code, outcome = str(exc), "ambiguous"
        except ConnectorError as exc:
            code, outcome = str(exc), "failed"
        except Exception:
            # Unexpected errors during writes are ambiguous, never silently treated as safe retries.
            logger.error("Job %s failed (%s)", job.id, job.kind)
            code, outcome = (
                "UNEXPECTED_WORKER_ERROR",
                "ambiguous" if job.kind == "post" else "failed",
            )
        finally:
            if adapter and hasattr(adapter, "close"):
                adapter.close()
        with self.sessions.begin() as db:
            # Fence stale workers: only the current lease owner can finish this attempt.
            changed = db.execute(
                update(Job)
                .where(Job.id == job_id, Job.lease_token == token, Job.state == "running")
                .values(lease_until=0)
            )
            if changed.rowcount != 1:
                return True
            job = db.get(Job, job_id)
            attempt = db.get(Attempt, attempt_id)
            attempt.finished_at, attempt.outcome = now(), outcome
            job.last_error = code
            document = db.get(Document, job.document_id) if job.document_id else None
            actor = Credential(
                id="system-worker",
                business_id=document.buyer_id if document else "system",
                environment=job.environment,
                role="operator",
                digest="",
            )
            if outcome == "success":
                job.state = "completed"
                if job.kind == "route" and document.status == "queued":
                    document.status = "delivered"
                    record(db, actor, "document.delivered", document)
                elif job.kind == "post":
                    document.status, document.posted_reference = "posted", reference
                    record(
                        db,
                        actor,
                        "document.posted",
                        document,
                        {
                            "reference": reference,
                            "mode": "simulated" if reference.startswith("sandbox:") else "provider",
                        },
                    )
            elif outcome == "ambiguous" and job.kind == "post":
                job.state = "needs_reconciliation"
                record(db, actor, "posting.ambiguous", document, {"job_id": job.id, "code": code})
            elif job.attempts >= self.settings.max_attempts:
                job.state = "dead_letter"
                record(db, actor, "job.dead_letter", document, {"job_id": job.id, "code": code})
            else:
                job.state = "pending"
                job.available_at = now() + min(3600, 2**job.attempts * 5) + random.randint(0, 5)
        return True

    def deliver_webhook(self, endpoint, event):
        if not endpoint or not endpoint.enabled:
            return
        # Exact operator-managed URLs, plus network egress filtering required in deployment.
        allowed = json.loads(os.environ.get("CBIN_WEBHOOK_ALLOWLIST", "[]"))
        if endpoint.url not in allowed:
            raise ConnectorError("WEBHOOK_EGRESS_NOT_APPROVED")
        secret = os.environ.get(endpoint.secret_ref)
        if not secret or len(secret) < 32:
            raise ConnectorError("WEBHOOK_SECRET_MISSING")
        body, timestamp = canonical_json(event).encode(), now()
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.post(
                    endpoint.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-CBIN-Event-ID": str(event["id"]),
                        "X-CBIN-Timestamp": str(timestamp),
                        "X-CBIN-Signature": sign_event(secret, timestamp, body),
                    },
                )
                if not 200 <= response.status_code < 300:
                    raise ConnectorError(f"WEBHOOK_HTTP_{response.status_code}")
        except httpx.TransportError:
            raise ConnectorError("WEBHOOK_NETWORK_FAILURE") from None


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    engine, sessions = make_database(settings.database_url)
    worker = Worker(settings, sessions)
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            if not worker.run_once():
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()
