import os

import pytest
from fastapi.testclient import TestClient

from cbin.api import create_app
from cbin.cli import provision
from cbin.config import Settings
from cbin.db import Base, Business, initialize


@pytest.fixture
def system(tmp_path):
    url = os.getenv("CBIN_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.db"
    settings = Settings(database_url=url, pepper="test-only-pepper-" + "x" * 32)
    app = create_app(settings)
    Base.metadata.drop_all(app.state.engine)
    initialize(app.state.engine)
    keys = {}
    with app.state.sessions.begin() as db:
        for business_id in ["seller", "buyer", "stranger", "unverified"]:
            db.add(
                Business(
                    id=business_id,
                    environment="test",
                    tin=f"TIN-{business_id}",
                    name=business_id,
                    registration=f"REG-{business_id}",
                    verified=business_id != "unverified",
                )
            )
        db.add(
            Business(
                id="live-buyer",
                environment="live",
                tin="TIN-live",
                name="Live",
                registration="LIVE",
                verified=True,
            )
        )
        db.flush()
        for business_id, role in [
            ("seller", "admin"),
            ("buyer", "admin"),
            ("stranger", "admin"),
            ("buyer", "operator"),
            ("seller", "reviewer"),
        ]:
            keys[f"{business_id}:{role}"] = provision(db, settings, business_id, role)
    with TestClient(app) as client:
        yield app, client, keys, settings
    app.state.engine.dispose()


@pytest.fixture
def invoice():
    return {
        "document_type": "B2B_INVOICE",
        "external_reference": "INV-001",
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
    }


def headers(keys, identity="seller:admin", key="submission-1"):
    return {"Authorization": f"Bearer {keys[identity]['key']}", "Idempotency-Key": key}
