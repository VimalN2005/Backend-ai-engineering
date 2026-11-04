"""
Day 49: SQLAlchemy 2.0 Core, Declarative Models & Engine Pooling.

This module provides a complete, modern SQLAlchemy 2.0 implementation demonstrating:
1. Engine Initialization & Connection Pool Configuration:
   - QueuePool configuration (pool_size, max_overflow, pool_timeout, pool_recycle, pool_pre_ping).
   - Event subsystem listeners (checkout/checkin metrics, cursor execution timer, slow query interceptor).
2. SQLAlchemy Core Schema Layer:
   - MetaData and Table programmatic definitions with explicit types, foreign keys, and indexes.
3. Modern 2.0 Declarative Models:
   - DeclarativeBase with PEP 484 Mapped[] and mapped_column() static typing.
   - Automatic nullability inference and composite __table_args__.
4. Core Expression Language (DML & DDL):
   - Type-safe select(), insert(), update(), delete() with programmatic joins and window functions.
   - Result extraction via scalars(), mappings(), and one_or_none().
5. High-Throughput Bulk Operations:
   - 2.0 insertmanyvalues batching optimization.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import QueuePool

# Configure module-level logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sqlalchemy_2_engine")


# ==============================================================================
# SECTION 1: CONNECTION POOL METRICS & EVENT SUBSYSTEM
# ==============================================================================

class ConnectionPoolMonitor:
    """Tracks connection pool telemetry using SQLAlchemy event hooks."""

    def __init__(self):
        self.total_checkouts = 0
        self.total_checkins = 0
        self.total_queries = 0
        self.slow_queries: List[Dict[str, Any]] = []

    def attach_to_engine(self, engine: Engine, slow_query_threshold_ms: float = 50.0) -> None:
        """Attaches diagnostic listeners to the SQLAlchemy engine."""

        @event.listens_for(engine, "checkout")
        def on_checkout(dbapi_connection, connection_record, connection_proxy):
            self.total_checkouts += 1
            connection_record.info["checkout_start"] = time.perf_counter()

        @event.listens_for(engine, "checkin")
        def on_checkin(dbapi_connection, connection_record):
            self.total_checkins += 1
            start = connection_record.info.pop("checkout_start", None)
            if start:
                hold_duration_ms = (time.perf_counter() - start) * 1000.0
                connection_record.info["last_hold_ms"] = hold_duration_ms

        @event.listens_for(engine, "before_cursor_execute")
        def on_before_execute(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("query_stack", []).append(time.perf_counter())

        @event.listens_for(engine, "after_cursor_execute")
        def on_after_execute(conn, cursor, statement, parameters, context, executemany):
            self.total_queries += 1
            stack = conn.info.get("query_stack", [])
            if stack:
                elapsed_ms = (time.perf_counter() - stack.pop()) * 1000.0
                if elapsed_ms > slow_query_threshold_ms:
                    self.slow_queries.append({
                        "statement": statement,
                        "parameters": parameters,
                        "elapsed_ms": round(elapsed_ms, 3)
                    })


# ==============================================================================
# SECTION 2: ENGINE INITIALIZATION & POOL CONFIGURATION
# ==============================================================================

def create_configured_engine(db_url: str = "sqlite:///enterprise.db") -> Tuple[Engine, ConnectionPoolMonitor]:
    """
    Initializes a production-grade SQLAlchemy 2.0 Engine with QueuePool.
    
    WHY: Explicitly calibrating pool_size, max_overflow, and pool_pre_ping
    prevents thread starvation, socket leaks, and stale TCP disconnect errors.
    """
    monitor = ConnectionPoolMonitor()

    engine = create_engine(
        db_url,
        # QueuePool Configuration
        poolclass=QueuePool,
        pool_size=5,             # 5 steady-state open connections
        max_overflow=10,         # Allow bursting up to 15 connections during traffic spikes
        pool_timeout=30.0,       # Wait up to 30 seconds before raising TimeoutError
        pool_recycle=1800,       # Recycle connections every 30 mins to avoid NAT firewall drops
        pool_pre_ping=True,      # Issue heartbeat test (SELECT 1) upon checkout
        echo=False,              # Set to False to prevent stdout flooding in production
    )

    monitor.attach_to_engine(engine)
    return engine, monitor


# ==============================================================================
# SECTION 3: CORE SCHEMA DEFINITION LAYER (MetaData & Table)
# ==============================================================================

core_metadata = MetaData()

# Programmatic Core Table 1: Tenants
tenants_table = Table(
    "core_tenants",
    core_metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    Column("created_at", DateTime, server_default=func.now()),
)

# Programmatic Core Table 2: Audit Ledger
audit_ledger_table = Table(
    "core_audit_ledger",
    core_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(36), ForeignKey("core_tenants.id", ondelete="CASCADE"), nullable=False),
    Column("action", String(50), nullable=False),
    Column("amount", Float, nullable=False, server_default="0.0"),
    Column("recorded_at", DateTime, server_default=func.now()),
    # Table Constraints & Composite Indexes
    Index("ix_audit_tenant_action", "tenant_id", "action"),
    CheckConstraint("amount >= 0.0", name="ck_audit_positive_amount"),
)


# ==============================================================================
# SECTION 4: MODERN 2.0 DECLARATIVE ORM MODELS (Mapped & mapped_column)
# ==============================================================================

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2.0 Declarative Models."""
    pass


class CustomerAccount(Base):
    """
    Customer entity modeled using SQLAlchemy 2.0 static typing.
    
    WHY: Mapped[] + mapped_column() binds runtime column constraints directly to
    Python static type hints (PEP 484), providing compile-time type safety.
    """
    __tablename__ = "orm_customer_accounts"

    # Primary Key
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    
    # Required String (nullable=False automatically inferred from 'str')
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    
    # Unique Email
    email: Mapped[str] = mapped_column(String(255))
    
    # Optional / Nullable String (nullable=True automatically inferred from 'Optional[str]')
    phone_number: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    
    # Balance with server default
    balance: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    
    # Boolean Status
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now()
    )

    # Multi-Column Table Arguments
    __table_args__ = (
        Index("ix_cust_tenant_email", "tenant_id", "email", unique=True),
        CheckConstraint("balance >= 0.0", name="ck_cust_balance_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<CustomerAccount id={self.id} tenant='{self.tenant_id}' email='{self.email}' balance={self.balance}>"


# ==============================================================================
# SECTION 5: CORE EXPRESSION PIPELINE & TRANSACTIONAL OPERATIONS
# ==============================================================================

class CoreLedgerService:
    """Encapsulates high-performance Core SQL operations with explicit transactions."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def initialize_schema(self) -> None:
        """Creates all Core and ORM tables in the database."""
        core_metadata.create_all(self.engine)
        Base.metadata.create_all(self.engine)

    def bulk_ingest_audit_events(self, events: List[Dict[str, Any]]) -> int:
        """
        Executes multi-row bulk insert using Core's 2.0 insertmanyvalues optimization.
        
        WHY: Core bulk inserts bypass ORM Identity Map tracking and object hydration,
        achieving 10x-50x higher throughput on large write pipelines.
        """
        with self.engine.begin() as conn:
            result = conn.execute(insert(audit_ledger_table), events)
            return result.rowcount or len(events)

    def compute_tenant_action_aggregations(self) -> List[Dict[str, Any]]:
        """
        Executes programmatic Core SELECT with JOIN, GROUP BY, and window functions.
        """
        # Window function: Running total amount per tenant
        running_amount = func.sum(audit_ledger_table.c.amount).over(
            partition_by=audit_ledger_table.c.tenant_id,
            order_by=audit_ledger_table.c.recorded_at.asc()
        ).label("cumulative_tenant_spend")

        stmt = (
            select(
                tenants_table.c.name.label("tenant_name"),
                audit_ledger_table.c.action,
                audit_ledger_table.c.amount,
                running_amount
            )
            .select_from(
                audit_ledger_table.join(
                    tenants_table,
                    audit_ledger_table.c.tenant_id == tenants_table.c.id
                )
            )
            .order_by(tenants_table.c.name, audit_ledger_table.c.recorded_at)
        )

        with self.engine.connect() as conn:
            # Result unwrapping via mappings() for fast dictionary consumption
            return [dict(row) for row in conn.execute(stmt).mappings()]


# ==============================================================================
# SECTION 6: ARCHITECTURAL VERIFICATION SUITE
# ==============================================================================

def run_sqlalchemy_2_demonstrations() -> None:
    print("=" * 72)
    print(" DAY 49: SQLALCHEMY 2.0 CORE, DECLARATIVE MODELS & POOLING")
    print("=" * 72)

    # 1. Initialize Engine & Pool Monitor
    engine, monitor = create_configured_engine("sqlite:///:memory:")
    service = CoreLedgerService(engine)
    service.initialize_schema()
    print("\n[STEP 1] Engine & QueuePool Initialized:")
    print(f"  -> Pool Type        : {engine.pool.__class__.__name__}")
    print(f"  -> Configured Size  : {engine.pool.size()}")
    print(f"  -> Max Overflow     : {engine.pool._max_overflow}")

    # 2. Populate Core Metadata Entities
    print("\n[STEP 2] Inserting Tenants via Explicit Core Transaction (engine.begin())...")
    tenants_data = [
        {"id": "t_acme", "name": "Acme Industrial", "is_active": True},
        {"id": "t_globex", "name": "Globex Corp", "is_active": True},
    ]
    with engine.begin() as conn:
        conn.execute(insert(tenants_table), tenants_data)

    # Verify via Core SELECT
    with engine.connect() as conn:
        tenants = conn.execute(select(tenants_table.c.id, tenants_table.c.name)).all()
        print(f"  -> Total Tenants Registered: {len(tenants)}")
        for t_id, t_name in tenants:
            print(f"     * Tenant [{t_id}]: {t_name}")
        assert len(tenants) == 2

    # 3. High-Throughput Bulk Insert via Core insertmanyvalues
    print("\n[STEP 3] Executing Bulk Ingest (1,000 Audit Events) via Core insert()...")
    events_payload = []
    actions = ["LOGIN", "PAYMENT", "EXPORT", "API_CALL"]
    for i in range(1, 1001):
        t_id = "t_acme" if i % 2 == 0 else "t_globex"
        events_payload.append({
            "tenant_id": t_id,
            "action": actions[i % len(actions)],
            "amount": round(10.0 + (i % 100) * 2.5, 2)
        })

    t0 = time.perf_counter()
    inserted_count = service.bulk_ingest_audit_events(events_payload)
    t_bulk = time.perf_counter() - t0
    print(f"  -> Ingested {inserted_count} events in {t_bulk * 1000:.2f} ms ({inserted_count / t_bulk:.1f} rows/sec)")
    assert inserted_count == 1000

    # 4. Complex Analytic Window Function Query in Core
    print("\n[STEP 4] Executing Relational Join & Window Function Query...")
    aggregated_rows = service.compute_tenant_action_aggregations()
    print(f"  -> Retrieved {len(aggregated_rows)} aggregated window rows.")
    print("  -> First 3 Window Calculations:")
    for row in aggregated_rows[:3]:
        print(f"     * {row['tenant_name']} | Action: {row['action']} | Amount: ${row['amount']:.2f} | Cumulative: ${row['cumulative_tenant_spend']:.2f}")
    assert len(aggregated_rows) == 1000

    # 5. Declarative ORM Model Operations
    print("\n[STEP 5] Validating Modern 2.0 Declarative Model (CustomerAccount)...")
    with Session(engine) as session:
        # Create ORM instances
        cust1 = CustomerAccount(
            tenant_id="t_acme",
            email="alice@acme.com",
            phone_number="+1-555-0100",
            balance=1500.0,
            is_verified=True
        )
        cust2 = CustomerAccount(
            tenant_id="t_globex",
            email="bob@globex.com",
            phone_number=None,  # Nullable inferred from Optional[str]
            balance=250.0,
            is_verified=False
        )
        session.add_all([cust1, cust2])
        session.commit()

        # Query using 2.0 unified select()
        stmt = (
            select(CustomerAccount)
            .where(CustomerAccount.tenant_id == "t_acme")
            .order_by(CustomerAccount.balance.desc())
        )
        acme_cust = session.execute(stmt).scalars().one_or_none()
        assert acme_cust is not None
        print(f"  -> Successfully Loaded ORM Entity: {acme_cust}")
        assert acme_cust.email == "alice@acme.com"
        assert acme_cust.balance == 1500.0

    # 6. Connection Pool & Event Subsystem Metrics
    print("\n[STEP 6] Connection Pool Telemetry Verification:")
    print(f"  -> Total Socket Checkouts : {monitor.total_checkouts}")
    print(f"  -> Total Socket Checkins  : {monitor.total_checkins}")
    print(f"  -> Total Queries Monitored: {monitor.total_queries}")
    assert monitor.total_checkouts > 0
    assert monitor.total_checkins == monitor.total_checkouts, "Connection leak detected in pool!"
    print("  -> Zero connection leaks verified (Checkouts == Checkins) [PASSED]")

    print("\n" + "=" * 72)
    print(" ALL DAY 49 SQLALCHEMY 2.0 CORE & DECLARATIVE TESTS PASSED SUCCESSFULLY!")
    print("=" * 72)


if __name__ == "__main__":
    run_sqlalchemy_2_demonstrations()
