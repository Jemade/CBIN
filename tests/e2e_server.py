"""Disposable loopback-only app for Playwright. Never imported by production CBIN."""

import json
import os
import tempfile
from pathlib import Path

import uvicorn
from sqlalchemy import select

from cbin.api import create_app
from cbin.cli import provision
from cbin.config import Settings
from cbin.connectors.sandbox import SandboxAdapter
from cbin.db import Base, Business, Decision, Document, initialize
from cbin.worker import Worker

scratch = tempfile.TemporaryDirectory(prefix="cbin-e2e-")
settings = Settings(
    database_url=f"sqlite:///{Path(scratch.name) / 'test.db'}",
    pepper="disposable-browser-test-pepper-" + "x" * 32,
)
os.environ["CBIN_CONNECTOR_CONFIG"] = json.dumps({"buyer": {"type": "sandbox"}})
app = create_app(settings)
keys = {}


def reset():
    Base.metadata.drop_all(app.state.engine)
    initialize(app.state.engine)
    keys.clear()
    with app.state.sessions.begin() as db:
        for name in ["seller", "buyer", "stranger"]:
            db.add(
                Business(
                    id=name,
                    environment="test",
                    tin=f"TIN-{name}",
                    name=f"Test {name}",
                    registration=f"REG-{name}",
                    verified=True,
                )
            )
        db.flush()
        for name in ["seller", "buyer", "stranger"]:
            keys[name] = provision(db, settings, name, "admin")["key"]
    return {
        "keys": keys,
        "invoice": {
            "document_type": "B2B_INVOICE",
            "external_reference": "BROWSER-001",
            "issued_at": "2026-10-04",
            "currency": "USD",
            "seller": {"cbin_id": "seller", "tin": "TIN-seller"},
            "buyer": {"cbin_id": "buyer", "tin": "TIN-buyer"},
            "line_items": [
                {
                    "item_code": "SKU-1",
                    "description": "Network cable",
                    "quantity": "2",
                    "unit_price_minor": 10000,
                    "tax_rate": "15",
                }
            ],
            "totals": {"subtotal_minor": 20000, "tax_minor": 3000, "grand_total_minor": 23000},
        },
    }


@app.post("/__test/reset")
def reset_for_test():
    return reset()


@app.post("/__test/drain")
def drain_for_test():
    worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
    for _ in range(100):
        if not worker.run_once():
            return {"drained": True}
    raise RuntimeError("Test worker failed to drain")


@app.get("/__test/decision/{document_id}")
def decision_for_test(document_id: str):
    with app.state.sessions() as db:
        decision = db.get(Decision, document_id)
        document = db.scalar(select(Document).where(Document.id == document_id))
        return {
            "status": document.status,
            "mapping": decision.mapping if decision else None,
            "posted_reference": document.posted_reference,
        }


if __name__ == "__main__":
    try:
        reset()
        uvicorn.run(app, host="127.0.0.1", port=8012)
    finally:
        scratch.cleanup()
