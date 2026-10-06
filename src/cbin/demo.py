"""Isolated, single-process demonstration. Never uses a business database or real ERP."""

import copy
import hmac
import json
import os
import secrets
import tempfile
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse

import cbin.connectors.registry as registry
from cbin.api import create_app
from cbin.cli import provision
from cbin.config import Settings
from cbin.connectors.base import ConnectorError
from cbin.connectors.sandbox import SandboxAdapter
from cbin.db import Business, initialize
from cbin.schemas import Invoice
from cbin.service import DomainError
from cbin.worker import Worker


def create_demo_app():
    if os.getenv("CBIN_ENVIRONMENT", "test") != "test":
        raise ValueError("The demonstration must never run in live mode")
    access = os.getenv("CBIN_DEMO_ACCESS_KEY", "")
    if len(access) < 32:
        raise ValueError("Set a strong CBIN_DEMO_ACCESS_KEY")
    scratch = tempfile.TemporaryDirectory(prefix="cbin-demo-")
    settings = Settings(
        database_url=f"sqlite:///{Path(scratch.name) / 'demo.db'}", pepper=secrets.token_hex(32)
    )
    os.environ["CBIN_CONNECTOR_CONFIG"] = json.dumps({"DEMO-BUYER": {"type": "sandbox"}})
    # Demo mode has no arbitrary export mappings, external webhooks or ERP credentials.
    os.environ["CBIN_IMPORT_PROFILES"] = "{}"
    os.environ["CBIN_ERP_ALLOWLIST"] = "[]"
    os.environ["CBIN_WEBHOOK_ALLOWLIST"] = "[]"
    os.environ["CBIN_OCR_CONFIG"] = "{}"
    os.environ["CBIN_FISCAL_VERIFIER_CONFIG"] = "{}"
    os.environ["CBIN_OCR_ALLOWLIST"] = "[]"
    os.environ["CBIN_FISCAL_VERIFIER_ALLOWLIST"] = "[]"
    os.environ["CBIN_REQUIRE_FISCAL_VERIFICATION"] = "false"
    app = create_app(settings)
    initialize(app.state.engine)
    workspaces = {}
    with app.state.sessions.begin() as db:
        for ident, name in [("DEMO-SELLER", "Demo supplier"), ("DEMO-BUYER", "Demo buyer")]:
            db.add(
                Business(
                    id=ident,
                    environment="test",
                    tin=f"TIN-{ident}",
                    name=name,
                    registration="SYNTHETIC-DEMO-ONLY",
                    verified=True,
                )
            )
        db.flush()
        for label, ident, role in [
            ("Seller", "DEMO-SELLER", "submitter"),
            ("Buyer", "DEMO-BUYER", "reviewer"),
            ("CBIN", "DEMO-BUYER", "operator"),
        ]:
            workspaces[label] = provision(db, settings, ident, role)["key"]
    payloads = {}
    for number in range(1, 4):
        payloads[str(number)] = {
            "document_type": "B2B_INVOICE",
            "external_reference": f"CBIN-DEMO-{number:03}",
            "issued_at": date.today().isoformat(),
            "currency": "USD",
            "seller": {"cbin_id": "DEMO-SELLER", "tin": "TIN-DEMO-SELLER"},
            "buyer": {"cbin_id": "DEMO-BUYER", "tin": "TIN-DEMO-BUYER"},
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
        }

    class DemoSource(SandboxAdapter):
        def source_documents(self):
            return [
                {
                    "source_id": ident,
                    "reference": value["external_reference"],
                    "issued_at": value["issued_at"],
                    "amount": "230.00",
                    "currency": "USD",
                }
                for ident, value in payloads.items()
            ]

        def source_invoice(self, ident):
            if ident not in payloads:
                raise ConnectorError("DEMO_SOURCE_NOT_FOUND")
            return Invoice.model_validate(copy.deepcopy(payloads[ident]))

    def demo_adapter(business, environment):
        if environment != "test" or business not in {"DEMO-SELLER", "DEMO-BUYER"}:
            raise ConnectorError("DEMO_BUSINESS_NOT_CONFIGURED")
        return DemoSource() if business == "DEMO-SELLER" else SandboxAdapter()

    # This factory is only entered by python -m cbin.demo, never by the production API.
    registry.adapter_for = demo_adapter
    worker = Worker(settings, app.state.sessions, demo_adapter)
    stop = threading.Event()

    def process():
        while not stop.is_set():
            if not worker.run_once():
                stop.wait(0.4)

    @asynccontextmanager
    async def lifespan(_):
        thread = threading.Thread(target=process, name="cbin-demo-worker", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=10)
            app.state.engine.dispose()
            scratch.cleanup()

    app.router.lifespan_context = lifespan

    @app.get("/__demo/session")
    def session(x_demo_access: str = Header(default="")):
        if not hmac.compare_digest(x_demo_access, access):
            raise DomainError("DEMO_ACCESS_REQUIRED", "Open your private demo link", 403)
        return JSONResponse(
            {
                "workspaces": workspaces,
                "notice": "Synthetic invoices · simulated ledger · resets when the service restarts",
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def notice(request, call_next):
        response = await call_next(request)
        response.headers["X-CBIN-Demo"] = "synthetic-data-simulated-ledger"
        if request.url.path.startswith(("/v1/", "/__demo/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.state.demo_workspaces = workspaces
    return app


if __name__ == "__main__":
    uvicorn.run(
        create_demo_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")), access_log=False
    )
