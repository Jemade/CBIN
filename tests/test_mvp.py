"""Functional multi-session checks; these do not establish production throughput."""

import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import headers
from sqlalchemy import inspect, select

from cbin.cli import main
from cbin.connectors.sandbox import SandboxAdapter
from cbin.db import Document, Job
from cbin.worker import Worker


def test_concurrent_submission_burst_and_parallel_worker_claims(system, invoice):
    app, client, keys, settings = system

    def send(number):
        payload = invoice | {"external_reference": f"BURST-{number}"}
        response = client.post(
            "/v1/documents", json=payload, headers=headers(keys, key=f"burst-{number}")
        )
        if response.status_code == 409:
            response = client.post(
                "/v1/documents", json=payload, headers=headers(keys, key=f"burst-{number}")
            )
        assert response.status_code == 202, response.text
        return response.json()["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = list(pool.map(send, range(20)))
    assert len(set(identifiers)) == 20

    def drain(_):
        worker = Worker(settings, app.state.sessions, lambda *_: SandboxAdapter())
        for _ in range(100):
            if not worker.run_once():
                break

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(drain, range(4)))
    with app.state.sessions() as db:
        docs = db.scalars(select(Document)).all()
        assert len(docs) == 20
        assert {doc.status for doc in docs} == {"delivered"}
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 20
        assert {job.state for job in jobs} == {"completed"}
        assert all(job.attempts == 1 for job in jobs)


def test_mvp_migration_is_repeatable_and_preserves_documents(system, invoice, monkeypatch, capsys):
    app, client, keys, settings = system
    ident = client.post("/v1/documents", json=invoice, headers=headers(keys)).json()["id"]
    index = next(i for i in Document.__table__.indexes if i.name == "ix_document_buyer_page")
    index.drop(app.state.engine)
    monkeypatch.setenv("CBIN_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("CBIN_CREDENTIAL_PEPPER", settings.pepper)
    monkeypatch.setattr(sys, "argv", ["cbin", "migrate-mvp"])
    main()
    main()
    assert "ix_document_buyer_page" in {
        i["name"] for i in inspect(app.state.engine).get_indexes("documents")
    }
    assert client.get(f"/v1/documents/{ident}", headers=headers(keys)).status_code == 200


def test_private_pilot_configuration_is_valid_and_never_overwrites_secrets(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "prepare_pilot", Path(__file__).parents[1] / "scripts/prepare_pilot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "pilot.env"
    module.prepare("192.168.1.20", "192.168.1.20", target)
    original = target.read_text()
    assert target.stat().st_mode & 0o777 == 0o600
    values = dict(line.split("=", 1) for line in original.splitlines())
    assert (
        json.loads(values["CBIN_CONNECTOR_CONFIG"].strip("'"))["CBIN-DEMO-BUYER"]["type"]
        == "sandbox"
    )
    assert len(values["CBIN_CREDENTIAL_PEPPER"].strip("'")) >= 32
    with pytest.raises(FileExistsError):
        module.prepare("different.example", "127.0.0.1", target)
    assert target.read_text() == original
