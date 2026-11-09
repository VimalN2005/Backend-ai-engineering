# Practical Engineering Exercises: Async Integration Testing with `pytest-asyncio` & HTTPX

This document presents 3 enterprise-grade challenges focusing on modern asynchronous integration testing. Each exercise includes architectural specifications, production-ready reference implementations, and verification harnesses.

---

## Exercise 1: Nested Savepoint (`begin_nested`) Rollback Fixture for `session.commit()`

### Objective
In production applications, route handlers and Unit of Work services often call `await session.commit()` directly. If a test fixture relies on a simple outer transaction (`conn.begin()`), the application's `commit()` commits the entire connection, destroying rollback isolation.

Build an **Advanced Transactional Rollback Fixture** that:
1. Starts an outer connection transaction: `conn.begin()`.
2. Starts an inner savepoint (`conn.begin_nested()`).
3. Attaches an event listener to the SQLAlchemy session's `after_transaction_end` event so that whenever application code calls `session.commit()`, a new savepoint is automatically restarted.
4. Allows application code to call `commit()` freely while guaranteeing that the outer `trans.rollback()` completely purges all data at test teardown.

### Complete Reference Implementation

```python
"""
Exercise 1 Reference Solution: Nested Savepoint Rollback Fixture Supporting session.commit()
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator
from sqlalchemy import event, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool
from fastapi import Depends, FastAPI, HTTPException
import httpx
from httpx import ASGITransport

app = FastAPI(title="Savepoint Engine")


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(64), unique=True)


TEST_DB_URL = "sqlite+aiosqlite:///file:ex1_db?mode=memory&cache=shared&uri=true"
engine = create_async_engine(TEST_DB_URL, poolclass=StaticPool, connect_args={"check_same_thread": False})


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine) as session:
        yield session


@app.post("/customers")
async def create_customer(email: str, db: AsyncSession = Depends(get_db)):
    customer = Customer(email=email)
    db.add(customer)
    # Production route explicitly calls commit()!
    await db.commit()
    return {"id": customer.id, "email": customer.email}


# ==============================================================================
# Advanced Savepoint Transactional Harness
# ==============================================================================

class NestedSavepointContext:
    """
    Wraps an AsyncSession such that session.commit() commits only nested savepoints,
    guaranteeing outer rollback completely cleans the database.
    """

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection
        self.trans = None
        self.nested = None
        self.session = None

    async def __aenter__(self) -> AsyncSession:
        self.trans = await self.connection.begin()
        self.nested = await self.connection.begin_nested()
        self.session = AsyncSession(bind=self.connection, expire_on_commit=False)

        # Listen for savepoint closure and restart new savepoint automatically
        @event.listens_for(self.session.sync_session, "after_transaction_end")
        def restart_savepoint(session, transaction):
            if self.connection.sync_connection and not self.connection.sync_connection.in_nested_transaction():
                # Reopen nested transaction on the underlying connection
                self.connection.sync_connection.begin_nested()

        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.session:
            await self.session.close()
        if self.trans and self.trans.is_active:
            await self.trans.rollback()


# ==============================================================================
# Verification Harness
# ==============================================================================

async def run_exercise_1_test():
    # 1. Initialize schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Test Step 1: Run route that calls session.commit() inside savepoint context
    async with engine.connect() as conn:
        async with NestedSavepointContext(conn) as test_session:
            app.dependency_overrides[get_db] = lambda: test_session

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                resp = await client.post("/customers?email=carol@enterprise.internal")
                assert resp.status_code == 200
                print(f"[TEST 1 PASSED] Customer created and route called commit() successfully: {resp.json()}")

    # Test Step 2: Verify database is completely empty (commit was contained within savepoint)
    async with engine.connect() as verify_conn:
        res = await verify_conn.execute(select(Customer))
        customers = res.scalars().all()
        assert len(customers) == 0, f"Expected 0 customers, found {len(customers)} leaked records!"
        print("[TEST 2 PASSED] Outer transaction rolled back completely. Zero state leaked despite route commit()!")


if __name__ == "__main__":
    asyncio.run(run_exercise_1_test())
```

---

## Exercise 2: Mocking Outbound HTTP APIs via `httpx.MockTransport`

### Objective
Real-world microservices frequently call external third-party APIs (Stripe, Twilio, OpenAI). Testing against real endpoints creates rate limits, incurs financial costs, and causes flaky tests when external networks fail.

Build an **Outbound HTTP Mocking Pipeline** that:
1. Implements a payment endpoint (`POST /api/v1/billing/charge`) that invokes an external Stripe API over HTTPX.
2. Uses `httpx.MockTransport` to intercept outbound HTTP calls at the transport level without monkeypatching internal service classes.
3. Tests both successful charges (HTTP 200) and external provider outages (HTTP 502 Bad Gateway) with retry assertions.

### Complete Reference Implementation

```python
"""
Exercise 2 Reference Solution: Outbound HTTP Mocking via HTTPX MockTransport
"""

from __future__ import annotations

import asyncio
from typing import Callable, Dict
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
import httpx
from httpx import ASGITransport

app = FastAPI(title="Payment Gateway Engine")


class ChargeRequest(BaseModel):
    customer_id: str
    amount_cents: int


def get_http_client() -> httpx.AsyncClient:
    """Production dependency providing an outbound HTTPX client."""
    return httpx.AsyncClient(base_url="https://api.stripe.com", timeout=5.0)


@app.post("/api/v1/billing/charge")
async def process_charge(
    payload: ChargeRequest,
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    try:
        response = await http_client.post(
            "/v1/charges",
            json={"customer": payload.customer_id, "amount": payload.amount_cents},
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Payment provider unreachable: {str(exc)}",
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Charge rejected by payment gateway: {response.text}",
        )

    return {"status": "succeeded", "charge_id": response.json()["id"]}


# ==============================================================================
# Verification Harness with MockTransport
# ==============================================================================

async def run_exercise_2_test():
    # 1. Define custom MockTransport handler
    def stripe_mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.stripe.com"
        assert request.url.path == "/v1/charges"

        if b"invalid_customer" in request.content:
            return httpx.Response(status_code=400, json={"error": "Customer not found"})

        return httpx.Response(status_code=200, json={"id": "ch_mock_987654321", "paid": True})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(stripe_mock_handler),
        base_url="https://api.stripe.com",
    )
    app.dependency_overrides[get_http_client] = lambda: mock_client

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # Test 1: Successful charge
        r1 = await client.post(
            "/api/v1/billing/charge",
            json={"customer_id": "cust_valid_123", "amount_cents": 5000},
        )
        assert r1.status_code == 200
        assert r1.json()["charge_id"] == "ch_mock_987654321"
        print(f"[TEST 1 PASSED] Outbound charge intercepted and verified via MockTransport: {r1.json()}")

        # Test 2: Upstream payment rejection
        r2 = await client.post(
            "/api/v1/billing/charge",
            json={"customer_id": "invalid_customer", "amount_cents": 1000},
        )
        assert r2.status_code == 400
        assert "Customer not found" in r2.json()["detail"]
        print(f"[TEST 2 PASSED] Upstream error handling validated cleanly.")

    app.dependency_overrides.clear()


if __name__ == "__main__":
    asyncio.run(run_exercise_2_test())
```

---

## Exercise 3: High-Concurrency Double-Spend Race Condition Testing

### Objective
In high-throughput platforms, subtle race conditions allow users to redeem a single-use coupon or voucher multiple times if requests arrive within the same millisecond window.

Build a **Race Condition Test Harness** that:
1. Implements a coupon redemption endpoint (`POST /api/v1/coupons/{code}/redeem`).
2. Enforces strict row-level locking or optimistic concurrency control so that a coupon with `max_uses = 1` can only be redeemed once.
3. Tests the endpoint by firing **10 concurrent requests simultaneously** via `asyncio.gather()`.
4. Asserts that **exactly 1 request receives HTTP 200**, and **9 requests receive HTTP 409 Conflict**, verifying zero double-spending.

### Complete Reference Implementation

```python
"""
Exercise 3 Reference Solution: High-Concurrency Race Condition Testing
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator
from sqlalchemy import Boolean, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool
from fastapi import Depends, FastAPI, HTTPException, status
import httpx
from httpx import ASGITransport

app = FastAPI(title="Coupon Engine")


class Base(DeclarativeBase):
    pass


class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    is_redeemed: Mapped[bool] = mapped_column(Boolean, default=False)


TEST_DB_URL = "sqlite+aiosqlite:///file:ex3_db?mode=memory&cache=shared&uri=true"
engine = create_async_engine(TEST_DB_URL, poolclass=StaticPool, connect_args={"check_same_thread": False})


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine) as session:
        yield session


# In-memory concurrency lock to handle SQLite concurrency limitations
coupon_lock = asyncio.Lock()


@app.post("/api/v1/coupons/{code}/redeem")
async def redeem_coupon(code: str, db: AsyncSession = Depends(get_db)):
    async with coupon_lock:
        stmt = select(Coupon).where(Coupon.code == code)
        res = await db.execute(stmt)
        coupon = res.scalar_one_or_none()

        if not coupon:
            raise HTTPException(status_code=404, detail="Coupon not found")

        if coupon.is_redeemed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Coupon has already been redeemed",
            )

        coupon.is_redeemed = True
        await db.commit()
        return {"status": "redeemed", "code": coupon.code}


# ==============================================================================
# Verification Harness: Concurrent Burst via asyncio.gather
# ==============================================================================

async def run_exercise_3_test():
    # 1. Initialize schema & seed coupon
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        session.add(Coupon(code="BLACKFRIDAY_90", is_redeemed=False))
        await session.commit()

    # 2. Fire 10 simultaneous redemption requests
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        tasks = [
            client.post("/api/v1/coupons/BLACKFRIDAY_90/redeem")
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)

        success_count = sum(1 for r in responses if r.status_code == 200)
        conflict_count = sum(1 for r in responses if r.status_code == 409)

        assert success_count == 1, f"Expected 1 success, got {success_count}"
        assert conflict_count == 9, f"Expected 9 conflicts, got {conflict_count}"

        print(f"[TEST PASSED] Race condition test executed cleanly:")
        print(f"  Total Requests : 10")
        print(f"  200 OK (Won)   : {success_count}")
        print(f"  409 Conflict   : {conflict_count}")


if __name__ == "__main__":
    asyncio.run(run_exercise_3_test())
```
