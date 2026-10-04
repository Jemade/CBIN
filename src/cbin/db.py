from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "businesses"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8))
    tin: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    registration: Mapped[str] = mapped_column(String(80))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("environment", "tin"),)


class Credential(Base):
    __tablename__ = "credentials"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(40))
    environment: Mapped[str] = mapped_column(String(8))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8))
    seller_id: Mapped[str] = mapped_column(String(40), index=True)
    buyer_id: Mapped[str] = mapped_column(String(40), index=True)
    external_reference: Mapped[str] = mapped_column(String(120))
    fingerprint: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[int] = mapped_column(Integer)
    posted_reference: Mapped[str | None] = mapped_column(String(150), nullable=True)
    __table_args__ = (UniqueConstraint("environment", "seller_id", "fingerprint"),)


class Idempotency(Base):
    __tablename__ = "idempotency"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment: Mapped[str] = mapped_column(String(8))
    business_id: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(String(40))
    __table_args__ = (UniqueConstraint("environment", "business_id", "key"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    environment: Mapped[str] = mapped_column(String(8))
    document_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    business_id: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(Integer)


class Decision(Base):
    __tablename__ = "buyer_decisions"
    document_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    actor: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(10))
    mapping: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Job(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    unique_key: Mapped[str] = mapped_column(String(150), unique=True)
    environment: Mapped[str] = mapped_column(String(8))
    document_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[int] = mapped_column(Integer, index=True)
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Attempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), default="started")


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8))
    business_id: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(String(2048))
    secret_ref: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


def make_database(url):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")

    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize(engine):
    # Initial schema only. Future changes require explicit, reviewed migrations.
    Base.metadata.create_all(engine)
