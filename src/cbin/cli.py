import argparse
import json

from sqlalchemy import select

from cbin.config import Settings
from cbin.db import (
    AccountingSnapshot,
    Business,
    BuyerMapping,
    Credential,
    Document,
    initialize,
    make_database,
)
from cbin.security import issue_key, key_digest
from cbin.service import record, uid


def provision(db, settings, business_id, role):
    business = db.get(Business, business_id)
    if not business or business.environment != settings.environment:
        raise ValueError("Business does not exist in this environment")
    key = issue_key(settings.environment)
    credential = Credential(
        id=uid(),
        business_id=business_id,
        environment=settings.environment,
        role=role,
        digest=key_digest(key, settings.pepper),
    )
    db.add(credential)
    record(db, credential, "credential.issued", data={"credential_id": credential.id, "role": role})
    return {"credential_id": credential.id, "business_id": business_id, "role": role, "key": key}


def main():
    parser = argparse.ArgumentParser(
        description="Trusted operator tools. Protect host and database access."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("migrate-bookkeeping")
    sub.add_parser("migrate-mvp")
    sub.add_parser("demo")
    business = sub.add_parser("add-business")
    business.add_argument("id")
    business.add_argument("--tin", required=True)
    business.add_argument("--name", required=True)
    business.add_argument("--registration", required=True)
    verify = sub.add_parser("verify-business")
    verify.add_argument("id")
    verify.add_argument(
        "--evidence", required=True, help="Reviewed evidence reference, not personal data"
    )
    verify.add_argument("--operator", required=True)
    key = sub.add_parser("issue-key")
    key.add_argument("business_id")
    key.add_argument(
        "--role", choices=["submitter", "reviewer", "admin", "operator"], required=True
    )
    revoke = sub.add_parser("revoke-key")
    revoke.add_argument("credential_id")
    revoke.add_argument("--operator", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    engine, sessions = make_database(settings.database_url)
    if args.command in {"migrate-bookkeeping", "migrate-mvp"}:
        for table in (AccountingSnapshot.__table__, BuyerMapping.__table__):
            table.create(engine, checkfirst=True)
        if args.command == "migrate-mvp":
            for index in Document.__table__.indexes:
                index.create(engine, checkfirst=True)
        print("Bookkeeping tables ready")
        return
    if args.command == "init-db":
        initialize(engine)
        print("Initial schema ready")
        return
    with sessions.begin() as db:
        if args.command == "demo":
            if settings.environment != "test":
                raise ValueError("Demo cannot provision live businesses")
            if db.scalar(select(Business.id).limit(1)):
                raise ValueError("Demo requires an empty database")
            for business_id, tin, name in [
                ("CBIN-DEMO-SELLER", "DEMO-TIN-SELLER", "Demo Supplier"),
                ("CBIN-DEMO-BUYER", "DEMO-TIN-BUYER", "Demo Buyer"),
            ]:
                db.add(
                    Business(
                        id=business_id,
                        environment="test",
                        tin=tin,
                        name=name,
                        registration="DEMO-ONLY",
                        verified=True,
                    )
                )
            db.flush()
            keys = [
                provision(db, settings, "CBIN-DEMO-SELLER", "submitter"),
                provision(db, settings, "CBIN-DEMO-BUYER", "reviewer"),
                provision(db, settings, "CBIN-DEMO-BUYER", "operator"),
            ]
            print(
                json.dumps(
                    {
                        "notice": "Test identities only. Keys are displayed once.",
                        "credentials": keys,
                    },
                    indent=2,
                )
            )
        elif args.command == "add-business":
            db.add(
                Business(
                    id=args.id,
                    environment=settings.environment,
                    tin=args.tin,
                    name=args.name,
                    registration=args.registration,
                    verified=False,
                )
            )
        elif args.command == "verify-business":
            business = db.get(Business, args.id)
            if not business or business.environment != settings.environment:
                raise ValueError("Business not found")
            business.verified = True
            actor = Credential(
                id=args.operator,
                business_id=business.id,
                environment=settings.environment,
                role="operator",
                digest="",
            )
            record(db, actor, "identity.verified", data={"evidence_reference": args.evidence})
        elif args.command == "issue-key":
            print(json.dumps(provision(db, settings, args.business_id, args.role)))
        elif args.command == "revoke-key":
            credential = db.get(Credential, args.credential_id)
            if not credential or credential.environment != settings.environment:
                raise ValueError("Credential not found")
            credential.revoked = True
            actor = Credential(
                id=args.operator,
                business_id=credential.business_id,
                environment=settings.environment,
                role="operator",
                digest="",
            )
            record(db, actor, "credential.revoked", data={"credential_id": credential.id})
    engine.dispose()


if __name__ == "__main__":
    main()
