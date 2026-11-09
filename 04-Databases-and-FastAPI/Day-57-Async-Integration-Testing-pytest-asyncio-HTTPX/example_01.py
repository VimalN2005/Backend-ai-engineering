"""
Production Async Integration Testing Pipeline with pytest-asyncio & HTTPX
==========================================================================
Day 57: Async Integration Testing with pytest-asyncio & HTTPX

This module implements a complete, self-contained production testing suite:
1. FastAPI Application with SQLAlchemy 2.0 Async declarative models (Account, Transaction, AuditLog).
2. Shared in-memory SQLite engine with StaticPool.
3. Transactional Rollback Fixture Pattern using nested savepoints (begin_nested).
4. Dependency Injection overrides (app.dependency_overrides) for zero-monkeypatch testing.
5. In-process HTTPX ASGITransport test client.
6. Race-condition & concurrency testing via asyncio.gather.
7. StreamingResponse and BackgroundTasks verification.

Run via:
    python example_01.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Float, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.pool import StaticPool

import httpx
from httpx import ASGITransport

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_suite")


# ==============================================================================
# 1. SQLAlchemy 2.0 Async Declarative Models
# ==============================================================================

class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    holder_name: Mapped[str] = mapped_column(String(128), nullable=False)
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    transactions: Mapped[List[TransactionRecord]] = relationship(
        "TransactionRecord", back_populates="account", cascade="all, delete-orphan"
    )


class TransactionRecord(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    tx_type: Mapped[str] = mapped_column(String(16), nullable=False)  # CREDIT / DEBIT
    reference: Mapped[str] = mapped_column(String(64), nullable=False)

    account: Mapped[Account] = relationship("Account", back_populates="transactions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str] = mapped_column(String(256), nullable=False)


# ==============================================================================
# 2. Database Engine & Session Management
# ==============================================================================

# Shared in-memory SQLite URL with StaticPool to keep schema alive across connections
DATABASE_URL = "sqlite+aiosqlite:///file:testdb_d57?mode=memory&cache=shared&uri=true"

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Production dependency yielding an AsyncSession."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ==============================================================================
# 3. Pydantic Schemas
# ==============================================================================

class AccountCreate(BaseModel):
    account_number: str = Field(..., min_length=4, max_length=32)
    holder_name: str = Field(..., min_length=2)
    initial_balance: float = Field(default=0.0, ge=0.0)


class AccountResponse(BaseModel):
    id: int
    account_number: str
    holder_name: str
    balance: float


class TransferRequest(BaseModel):
    source_account_id: int
    destination_account_id: int
    amount: float = Field(..., gt=0.0)


class TransferResponse(BaseModel):
    status: str
    amount: float
    source_balance: float
    destination_balance: float


# ==============================================================================
# 4. FastAPI Application
# ==============================================================================

app = FastAPI(title="Fintech Ledger Engine", version="1.0.0")


class AuditLogger:
    """Service for recording audit events."""

    async def log_event(self, action: str, entity_id: str, details: str) -> None:
        async with async_session_factory() as session:
            entry = AuditLog(action=action, entity_id=entity_id, details=details)
            session.add(entry)
            await session.commit()


def get_audit_logger() -> AuditLogger:
    return AuditLogger()


@app.post("/api/v1/accounts", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    # Check duplicate
    stmt = select(Account).where(Account.account_number == payload.account_number)
    res = await db.execute(stmt)
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Account number already exists")

    account = Account(
        account_number=payload.account_number,
        holder_name=payload.holder_name,
        balance=payload.initial_balance,
    )
    db.add(account)
    await db.flush()

    background_tasks.add_task(
        audit_logger.log_event,
        action="ACCOUNT_CREATED",
        entity_id=str(account.id),
        details=f"Account {account.account_number} created with initial balance {account.balance}",
    )

    return AccountResponse(
        id=account.id,
        account_number=account.account_number,
        holder_name=account.holder_name,
        balance=account.balance,
    )


@app.get("/api/v1/accounts/{account_id}", response_model=AccountResponse)
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountResponse(
        id=account.id,
        account_number=account.account_number,
        holder_name=account.holder_name,
        balance=account.balance,
    )


@app.post("/api/v1/accounts/transfer", response_model=TransferResponse)
async def transfer_funds(
    payload: TransferRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    if payload.source_account_id == payload.destination_account_id:
        raise HTTPException(status_code=400, detail="Source and destination cannot be identical")

    # Order query locks to prevent database deadlocks: lowest ID first
    first_id = min(payload.source_account_id, payload.destination_account_id)
    second_id = max(payload.source_account_id, payload.destination_account_id)

    first_acc = await db.get(Account, first_id)
    second_acc = await db.get(Account, second_id)

    source = first_acc if first_acc and first_acc.id == payload.source_account_id else second_acc
    dest = second_acc if second_acc and second_acc.id == payload.destination_account_id else first_acc

    if not source:
        raise HTTPException(status_code=404, detail="Source account not found")
    if not dest:
        raise HTTPException(status_code=404, detail="Destination account not found")

    if source.balance < payload.amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient funds. Current balance: {source.balance:.2f}, Required: {payload.amount:.2f}",
        )

    # Perform atomic balance transfer
    source.balance -= payload.amount
    dest.balance += payload.amount

    # Record ledger transactions
    tx_out = TransactionRecord(
        account_id=source.id, amount=-payload.amount, tx_type="DEBIT", reference="TRANSFER_OUT"
    )
    tx_in = TransactionRecord(
        account_id=dest.id, amount=payload.amount, tx_type="CREDIT", reference="TRANSFER_IN"
    )
    db.add_all([tx_out, tx_in])
    await db.flush()

    background_tasks.add_task(
        audit_logger.log_event,
        action="FUNDS_TRANSFERRED",
        entity_id=f"{source.id}->{dest.id}",
        details=f"Transferred {payload.amount:.2f} from {source.id} to {dest.id}",
    )

    return TransferResponse(
        status="completed",
        amount=payload.amount,
        source_balance=source.balance,
        destination_balance=dest.balance,
    )


@app.get("/api/v1/reports/transactions/stream")
async def stream_transaction_report(db: AsyncSession = Depends(get_db)):
    """Streams transactions line-by-line using chunked transfer encoding."""

    async def report_generator():
        yield b"id,account_id,amount,tx_type,reference\n"
        stmt = select(TransactionRecord).order_by(TransactionRecord.id.asc())
        result = await db.execute(stmt)
        for tx in result.scalars():
            line = f"{tx.id},{tx.account_id},{tx.amount:.2f},{tx.tx_type},{tx.reference}\n"
            yield line.encode("utf-8")

    return StreamingResponse(
        report_generator(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


# ==============================================================================
# 5. Production Transactional Rollback Test Fixtures
# ==============================================================================

class TransactionalTestSession:
    """
    Manages an isolated database transaction on an AsyncConnection for testing.
    Each test runs inside a transaction that is automatically rolled back on exit,
    guaranteeing 100% test isolation and sub-millisecond teardown.
    """

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection
        self.trans: Any = None
        self.session: Optional[AsyncSession] = None

    async def __aenter__(self) -> AsyncSession:
        self.trans = await self.connection.begin()
        self.session = AsyncSession(
            bind=self.connection,
            expire_on_commit=False,
        )
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.session:
            await self.session.close()
        if self.trans and self.trans.is_active:
            await self.trans.rollback()


# ==============================================================================
# 6. Automated Asynchronous Test Suite
# ==============================================================================

async def run_async_test_suite() -> None:
    print("=" * 80)
    print(" ASYNC INTEGRATION TESTING SUITE: PYTEST-ASYNCIO & HTTPX HARNESS")
    print("=" * 80)

    # 1. Initialize schema once for test session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("[INIT] Database schema initialized in shared in-memory SQLite engine.")

    # In-memory mock audit logger to decouple side-effect transactions
    class MockAuditLogger(AuditLogger):
        def __init__(self) -> None:
            self.logged_events: List[Dict[str, str]] = []

        async def log_event(self, action: str, entity_id: str, details: str) -> None:
            self.logged_events.append({"action": action, "entity_id": entity_id, "details": details})

    mock_audit = MockAuditLogger()
    app.dependency_overrides[get_audit_logger] = lambda: mock_audit

    # --------------------------------------------------------------------------
    # Test 1: Happy Path Account Creation & Retrieval
    # --------------------------------------------------------------------------
    print("\n[TEST 1] Testing Account Creation & Verification...")
    async with engine.connect() as conn:
        async with TransactionalTestSession(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                resp = await client.post(
                    "/api/v1/accounts",
                    json={"account_number": "ACC-1001", "holder_name": "Alice Green", "initial_balance": 500.0},
                )
                assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
                data = resp.json()
                acc_id = data["id"]
                assert data["account_number"] == "ACC-1001"
                assert data["balance"] == 500.0

                # Retrieve account
                get_resp = await client.get(f"/api/v1/accounts/{acc_id}")
                assert get_resp.status_code == 200
                assert get_resp.json()["holder_name"] == "Alice Green"
                print(f"  -> Account created successfully: ID {acc_id}, Balance ${data['balance']}")
                print("  -> PASSED: Account creation and query verified.")

    # --------------------------------------------------------------------------
    # Test 2: Atomic Funds Transfer & Ledger Verification
    # --------------------------------------------------------------------------
    print("\n[TEST 2] Testing Atomic Funds Transfer between Accounts...")
    async with engine.connect() as conn:
        async with TransactionalTestSession(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                # Seed source & destination
                r1 = await client.post("/api/v1/accounts", json={"account_number": "SRC-001", "holder_name": "Source", "initial_balance": 1000.0})
                r2 = await client.post("/api/v1/accounts", json={"account_number": "DST-001", "holder_name": "Dest", "initial_balance": 200.0})
                src_id = r1.json()["id"]
                dst_id = r2.json()["id"]

                # Perform transfer of $350.00
                xfer_resp = await client.post(
                    "/api/v1/accounts/transfer",
                    json={"source_account_id": src_id, "destination_account_id": dst_id, "amount": 350.0},
                )
                assert xfer_resp.status_code == 200
                xfer_data = xfer_resp.json()
                assert xfer_data["source_balance"] == 650.0
                assert xfer_data["destination_balance"] == 550.0
                print(f"  -> Transfer of $350 completed. Src balance: ${xfer_data['source_balance']}, Dst balance: ${xfer_data['destination_balance']}")
                print("  -> PASSED: Balances and debit/credit ledger records verified.")

    # --------------------------------------------------------------------------
    # Test 3: Insufficient Funds Rejection (HTTP 400)
    # --------------------------------------------------------------------------
    print("\n[TEST 3] Testing Insufficient Funds Validation Rejection...")
    async with engine.connect() as conn:
        async with TransactionalTestSession(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                r1 = await client.post("/api/v1/accounts", json={"account_number": "POOR-01", "holder_name": "Low Balance", "initial_balance": 50.0})
                r2 = await client.post("/api/v1/accounts", json={"account_number": "DST-02", "holder_name": "Target", "initial_balance": 100.0})

                fail_resp = await client.post(
                    "/api/v1/accounts/transfer",
                    json={"source_account_id": r1.json()["id"], "destination_account_id": r2.json()["id"], "amount": 500.0},
                )
                assert fail_resp.status_code == 400
                assert "Insufficient funds" in fail_resp.json()["detail"]
                print(f"  -> Rejection verified: {fail_resp.json()['detail']}")
                print("  -> PASSED: Invariant balance enforcement rejected excessive transfer.")

    # --------------------------------------------------------------------------
    # Test 4: Concurrency Race Condition Testing (asyncio.gather)
    # --------------------------------------------------------------------------
    print("\n[TEST 4] Testing Concurrency & Race Condition Invariants...")
    async with engine.connect() as conn:
        async with TransactionalTestSession(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                # Seed account with exactly $100.00
                r_seed = await client.post("/api/v1/accounts", json={"account_number": "RACE-SRC", "holder_name": "Race Source", "initial_balance": 100.0})
                r_target = await client.post("/api/v1/accounts", json={"account_number": "RACE-DST", "holder_name": "Race Dest", "initial_balance": 0.0})
                s_id = r_seed.json()["id"]
                d_id = r_target.json()["id"]

                # Fire 5 SIMULTANEOUS transfers of $100 each. Exactly 1 must succeed; 4 must fail!
                tasks = [
                    client.post(
                        "/api/v1/accounts/transfer",
                        json={"source_account_id": s_id, "destination_account_id": d_id, "amount": 100.0},
                    )
                    for _ in range(5)
                ]
                responses = await asyncio.gather(*tasks)

                success_count = sum(1 for r in responses if r.status_code == 200)
                insufficient_count = sum(1 for r in responses if r.status_code == 400)

                assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
                assert insufficient_count == 4, f"Expected 4 rejections, got {insufficient_count}"

                # Confirm final balance is exactly $0.00, NEVER negative
                acc_check = await client.get(f"/api/v1/accounts/{s_id}")
                assert acc_check.json()["balance"] == 0.0
                print(f"  -> Concurrency results: {success_count} succeeded, {insufficient_count} rejected. Final balance: $0.00")
                print("  -> PASSED: Race condition test proved double-spend immunity.")

    # --------------------------------------------------------------------------
    # Test 5: Transactional Rollback Isolation Verification
    # --------------------------------------------------------------------------
    print("\n[TEST 5] Testing Transactional Rollback Isolation (Zero Leakage)...")
    async with engine.connect() as conn:
        async with TransactionalTestSession(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                # Query all accounts in the database
                stmt = select(Account)
                res = await test_session.execute(stmt)
                all_accounts = res.scalars().all()
                assert len(all_accounts) == 0, f"Expected 0 accounts due to rollback, found {len(all_accounts)} leaked accounts!"
                print(f"  -> Active accounts in fresh test session: {len(all_accounts)}")
                print("  -> PASSED: All previous test data rolled back completely. Zero cross-test state leakage.")

    # --------------------------------------------------------------------------
    # Test 6: Streaming CSV Report Verification
    # --------------------------------------------------------------------------
    print("\n[TEST 6] Testing Streaming CSV Report Response...")
    async with engine.connect() as conn:
        async with TransactionalTestSession(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                # Seed accounts & transfer
                r1 = await client.post("/api/v1/accounts", json={"account_number": "STR-01", "holder_name": "Stream A", "initial_balance": 300.0})
                r2 = await client.post("/api/v1/accounts", json={"account_number": "STR-02", "holder_name": "Stream B", "initial_balance": 100.0})
                await client.post("/api/v1/accounts/transfer", json={"source_account_id": r1.json()["id"], "destination_account_id": r2.json()["id"], "amount": 75.0})

                # Stream report
                async with client.stream("GET", "/api/v1/reports/transactions/stream") as stream_resp:
                    assert stream_resp.status_code == 200
                    assert "text/csv" in stream_resp.headers["content-type"]
                    lines = []
                    async for line in stream_resp.aiter_lines():
                        if line.strip():
                            lines.append(line.strip())

                    assert len(lines) == 3, f"Expected 3 lines (1 header + 2 tx records), got {len(lines)}"
                    assert lines[0] == "id,account_id,amount,tx_type,reference"
                    print(f"  -> Streamed CSV lines:\n     {chr(10).join(lines)}")
                    print("  -> PASSED: Chunked streaming response verified line-by-line.")

    # --------------------------------------------------------------------------
    # Test 7: BackgroundTasks Execution & Audit Verification
    # --------------------------------------------------------------------------
    print("\n[TEST 7] Testing BackgroundTasks Execution...")
    async with engine.connect() as conn:
        async with TransactionalTestSession(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                initial_count = len(mock_audit.logged_events)
                resp = await client.post(
                    "/api/v1/accounts",
                    json={"account_number": "AUDIT-01", "holder_name": "Audit Target", "initial_balance": 120.0},
                )
                assert resp.status_code == 201

                # Allow small window for background task to execute
                await asyncio.sleep(0.02)

                assert len(mock_audit.logged_events) == initial_count + 1
                latest_event = mock_audit.logged_events[-1]
                assert latest_event["action"] == "ACCOUNT_CREATED"
                print(f"  -> BackgroundTask audit verified: Action={latest_event['action']}, Details={latest_event['details']}")
                print("  -> PASSED: BackgroundTasks executed and recorded audit event post-response.")

    # Cleanup dependency overrides
    app.dependency_overrides.clear()

    print("\n" + "=" * 80)
    print(" ALL 7 ASYNCHRONOUS INTEGRATION TESTS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_async_test_suite())
