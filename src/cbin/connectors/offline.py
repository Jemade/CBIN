"""Durable connector-side spool. Keep this database on a protected persistent volume."""

import json
import sqlite3

import httpx

from cbin.schemas import Invoice
from cbin.service import canonical_json, digest


class OfflineSpool:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS spool (sequence INTEGER PRIMARY KEY, "
            "key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT)"
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def enqueue(self, key, payload):
        payload = Invoice.model_validate(payload).model_dump(mode="json")
        existing = self.db.execute("SELECT payload FROM spool WHERE key=?", (key,)).fetchone()
        if existing and digest(json.loads(existing[0])) != digest(payload):
            raise ValueError("Offline idempotency conflict")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO spool(key,payload,state) VALUES(?,?, 'pending')",
                (key, canonical_json(payload)),
            )

    def flush_one(self, client: httpx.Client, base_url, credential):
        row = self.db.execute(
            "SELECT sequence,key,payload FROM spool WHERE state='pending' ORDER BY sequence LIMIT 1"
        ).fetchone()
        if not row:
            return False
        sequence, key, payload = row
        try:
            response = client.post(
                base_url.rstrip("/") + "/v1/documents",
                content=payload,
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": key,
                },
            )
        except httpx.TransportError:
            return False  # Leave queued. CBIN handles ambiguous acknowledgements by key.
        with self.db:
            if response.status_code == 202:
                self.db.execute(
                    "UPDATE spool SET state='delivered',error=NULL WHERE sequence=?", (sequence,)
                )
            elif response.status_code in {400, 403, 404, 422}:
                self.db.execute(
                    "UPDATE spool SET state='blocked',error=? WHERE sequence=?",
                    (f"HTTP_{response.status_code}", sequence),
                )
            else:
                return False  # Auth, throttling, concurrency conflicts and server errors require recovery.
        return True
