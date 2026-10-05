"""Prepare private demo configuration; does not start or replace an existing deployment."""

import argparse
import json
import os
import secrets
from pathlib import Path

from cbin.connectors.mapping import MappingProfile


def prepare(host, bind, target):
    profile = {
        "reviewed": True,
        "evidence_reference": "CBIN-CANONICAL-DEMO-ONLY",
        "fields": {
            name: {"path": name}
            for name in [
                "document_type",
                "external_reference",
                "issued_at",
                "currency",
                "seller",
                "buyer",
                "totals",
            ]
        },
        "line_items_path": "line_items",
        "line_fields": {
            name: {"path": name}
            for name in ["item_code", "description", "quantity", "unit_price_minor", "tax_rate"]
        },
    }
    MappingProfile.model_validate(profile)
    rows = {
        "CBIN_DB_PASSWORD": secrets.token_hex(24),
        "CBIN_CREDENTIAL_PEPPER": secrets.token_hex(32),
        "CBIN_PILOT_HOST": host,
        "CBIN_PILOT_BIND": bind,
        "CBIN_CONNECTOR_CONFIG": json.dumps(
            {"CBIN-DEMO-BUYER": {"type": "sandbox"}}, separators=(",", ":")
        ),
        "CBIN_IMPORT_PROFILES": json.dumps(
            {"test": {"CBIN-DEMO-SELLER": {"excel": {"canonical-demo": profile}}}},
            separators=(",", ":"),
        ),
    }
    # Exclusive creation preserves an existing pepper/database password.
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        for key, value in rows.items():
            if any(char in value for char in "\n\r'$"):
                raise ValueError("Unsupported configuration character")
            stream.write(f"{key}='{value}'\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", required=True, help="Private LAN IP or DNS name reachable from both machines"
    )
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Use the host's private LAN interface for two-machine access",
    )
    parser.add_argument("--output", default=".env.pilot")
    args = parser.parse_args()
    prepare(args.host, args.bind, Path(args.output))
    print("Pilot configuration prepared. Follow docs/TWO-MACHINE-PILOT.md.")


if __name__ == "__main__":
    main()
