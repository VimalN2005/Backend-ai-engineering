# Critical Antipatterns & Pitfalls in Async Integration Testing

This document analyzes the 7 most destructive antipatterns and architectural pitfalls encountered when writing asynchronous integration tests with `pytest-asyncio`, HTTPX, and SQLAlchemy in FastAPI applications.

---

## 1. Recreating the Database Schema (`drop_all()` / `create_all()`) Per Test

### Failure Mechanism
Executing Data Definition Language (DDL) commands (`CREATE TABLE`, `DROP TABLE`, `ALTER TABLE`) is among the most computationally expensive operations in relational database engines. DDL invalidates catalog caches, acquires exclusive schema locks, and performs synchronous disk syncs.

When developers implement test fixtures that drop and recreate tables for every single test function:
- A test suite with 200 tests takes **30 to 60 seconds** instead of **1 to 2 seconds**.
- CI/CD build times balloon, and developers stop running tests locally before committing.

### Vulnerable Implementation
```python
# CRITICAL ANTIPATTERN: Dropping and recreating tables for every test (Slows suite by 50x)
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

@pytest.fixture(autouse=True)
async def reset_database_per_test(engine):
    # Runs DDL on EVERY test function!
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
```

### Production Remediation: Transactional Rollback
Create the schema **once** for the entire test session. For each test, open an `AsyncConnection`, begin a transaction, bind an `AsyncSession`, and **roll back** the transaction during fixture teardown. All test changes are discarded instantaneously in memory without running a single DDL command.

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

@pytest.fixture(scope="session", autouse=True)
async def init_test_schema(engine):
    # DDL executed exactly ONCE for the entire test session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture
async def db_session(engine):
    # Ultra-fast transactional rollback (< 1ms per test)
    async with engine.connect() as connection:
        trans = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
```

---

## 2. Using Starlette's `TestClient` in Async Integration Suites Instead of HTTPX `ASGITransport`

### Failure Mechanism
Starlette's `TestClient` is a synchronous wrapper built on top of AnyIO portals. When `client.get()` is invoked:
1. `TestClient` spawns a **separate background OS thread** to run an asynchronous event loop.
2. The FastAPI route executes on that background thread, while your test function runs on the main pytest thread.

This thread boundary causes two fatal failures:
- **Database Transaction Sharing Fails**: You cannot pass an in-memory connection or uncommitted transaction across threads. When the route handler queries the database, it uses a different connection and cannot see uncommitted seed data created by the test fixture.
- **Deadlocks on Shared In-Memory DBs**: In-memory SQLite with `StaticPool` deadlocks if the main thread holds a write lock while the background app thread attempts to read or write.

### Vulnerable Implementation
```python
# CRITICAL ANTIPATTERN: TestClient executes on a background thread, breaking transaction sharing
from fastapi.testclient import TestClient
import pytest

@pytest.mark.asyncio
async def test_order_status(app, db_session):
    # db_session is created on the main async event loop
    client = TestClient(app)  # Runs app on a separate AnyIO worker thread!
    
    # DEADLOCK or "no such table" error due to thread isolation
    resp = client.get("/api/v1/orders/1")
```

### Production Remediation: Native HTTPX `ASGITransport`
Use `httpx.AsyncClient` with `ASGITransport(app=app)`. Both the test function and the ASGI application execute cooperatively on the **exact same event loop and OS thread**, allowing seamless transaction sharing and zero context loss.

```python
import httpx
from httpx import ASGITransport
import pytest

@pytest.fixture
async def async_client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

@pytest.mark.asyncio
async def test_order_status(async_client, db_session):
    # Executes cooperatively on the identical event loop
    resp = await async_client.get("/api/v1/orders/1")
    assert resp.status_code == 200
```

---

## 3. Flawed In-Memory SQLite Setup without Shared Cache & `StaticPool`

### Failure Mechanism
When using SQLite in-memory databases for fast local tests, developers frequently use the standard connection string:
`sqlite+aiosqlite:///:memory:`

In SQLite, `:memory:` creates a database that belongs exclusively to that specific connection instance. When SQLAlchemy's connection pool closes or recycles a connection, the database is **wiped from memory immediately**. When the next query executes, it connects to a brand new, empty database and throws:
`sqlite3.OperationalError: no such table: users`

### Vulnerable Implementation
```python
# CRITICAL BUG: Standard in-memory SQLite destroys tables on connection close
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Default pool drops connection after request -> tables disappear!
engine = create_async_engine(TEST_DB_URL)
```

### Production Remediation
Use SQLite's **URI Shared-Cache Mode** (`file:memdb?mode=memory&cache=shared&uri=true`) combined with SQLAlchemy's `StaticPool`. `StaticPool` keeps a single physical connection pinned in memory across all sessions, preventing table destruction.

```python
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

# Shared cache name ensures distinct test runs don't collide
TEST_DB_URL = "sqlite+aiosqlite:///file:testdb_isolated?mode=memory&cache=shared&uri=true"

engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # Holds the identical connection open for all checkouts
)
```

---

## 4. Leaking Background Task Commits into Shared Connection Pools

### Failure Mechanism
In an in-memory testing environment with `StaticPool`, all sessions checkout the **exact same underlying physical database connection**.

If a route registers a `BackgroundTask` (e.g., logging an audit event) that opens a fresh session and calls `await session.commit()`, that `commit()` is executed on the **shared connection**. This commits **all uncommitted changes** from the main test function, completely breaking the transactional rollback fixture and permanently leaking records into subsequent tests!

### Vulnerable Implementation
```python
# CRITICAL LEAK: Background task commits the shared connection!
async def decoupled_audit_task(user_id: int):
    async with async_session_factory() as session:
        session.add(AuditLog(user_id=user_id, action="LOGIN"))
        # FATAL: Commits the entire shared connection, ruining test rollback isolation!
        await session.commit()

@app.post("/login")
async def login(background_tasks: BackgroundTasks):
    background_tasks.add_task(decoupled_audit_task, 42)
    return {"status": "ok"}
```

### Production Remediation
Use FastAPI's `app.dependency_overrides` to inject a decoupled mock or test-specific audit logger during transactional tests. This isolates side-effects and prevents accidental connection-level commits.

```python
class TestAuditLogger:
    def __init__(self):
        self.events = []
    async def log_event(self, action: str, entity_id: str):
        self.events.append((action, entity_id))

@pytest.fixture
def mock_audit(app):
    audit = TestAuditLogger()
    app.dependency_overrides[get_audit_logger] = lambda: audit
    yield audit
    app.dependency_overrides.pop(get_audit_logger, None)
```

---

## 5. Overusing `unittest.mock.patch` Instead of `app.dependency_overrides`

### Failure Mechanism
Developers accustomed to legacy Django or Flask testing often resort to `unittest.mock.patch` to mock authentication or database calls:
`@patch("app.api.dependencies.get_current_user")`

In asynchronous FastAPI applications, monkeypatching causes several issues:
1. **Module Import Paths Break**: If an endpoint imports `get_current_user` directly (`from app.auth import get_current_user`), patching `app.api.dependencies.get_current_user` has zero effect.
2. **Race Conditions in Parallel Tests**: `patch` mutates the global module namespace, breaking concurrent tests running under `pytest -n auto`.
3. **Leaked State**: If an exception occurs before the `patch` unbinds, the mock persists into subsequent tests.

### Production Remediation: FastAPI `dependency_overrides`
FastAPI has first-class support for test doubles via `app.dependency_overrides`. It is scoped, type-safe, and does not alter Python runtime module globals.

```python
@pytest.fixture
def auth_client(app, async_client):
    test_user = User(id=1, email="admin@enterprise.internal", role="admin")
    # Clean, targeted dependency override
    app.dependency_overrides[get_current_active_user] = lambda: test_user
    yield async_client
    # Deterministic cleanup in teardown
    app.dependency_overrides.pop(get_current_active_user, None)
```

---

## 6. Committing (`session.commit()`) in Test Seed Fixtures Instead of Flushing (`session.flush()`)

### Failure Mechanism
When writing test fixtures to seed test data (e.g., creating a tenant, customer, or account), calling `await session.commit()` permanently writes those rows to the database.

Because the transaction was committed, the outer rollback fixture (`await trans.rollback()`) can no longer undo those insertions. The seeded data remains in the database, causing primary key conflicts (`IntegrityError: UNIQUE constraint failed`) in subsequent tests.

### Vulnerable Implementation
```python
# CRITICAL BUG: Committing inside seed fixtures breaks rollback isolation
@pytest.fixture
async def seed_account(db_session: AsyncSession):
    account = Account(account_number="ACC-999", balance=100.0)
    db_session.add(account)
    # FATAL ERROR: Commits the transaction to the database!
    await db_session.commit()
    return account
```

### Production Remediation: Use `await session.flush()`
Call `await session.flush()` instead of `commit()`. Flushing executes SQL `INSERT` statements within the open transaction, assigning autoincrement primary keys and default column values, while keeping the data safely inside the rollbackable transaction.

```python
@pytest.fixture
async def seed_account(db_session: AsyncSession):
    account = Account(account_number="ACC-999", balance=100.0)
    db_session.add(account)
    # SAFE: Generates ID and validates constraints without committing outer transaction
    await db_session.flush()
    return account
```

---

## 7. Bypassing Application Lifespan Handlers in `ASGITransport`

### Failure Mechanism
When passing an application directly to `httpx.ASGITransport(app=app)`, HTTPX executes incoming HTTP requests directly against the ASGI callable. However, **it does not trigger application lifespan events** (`lifespan` context manager or `startup`/`shutdown` handlers).

If your FastAPI application initializes database connection pools, Redis clients, or HTTP connection pools inside `lifespan`:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = await aioredis.from_url(...)
    yield
    await app.state.redis.close()
```
Running tests directly through `ASGITransport` results in `AttributeError: 'State' object has no attribute 'redis'`!

### Production Remediation
Use an explicit lifespan context fixture or wrap `ASGITransport` in an async client that enters the lifespan context manager:

```python
import pytest
import httpx
from httpx import ASGITransport

@pytest.fixture(scope="session")
async def app_lifespan(app):
    # Explicitly execute application startup and shutdown lifespans
    async with app.router.lifespan_context(app):
        yield app

@pytest.fixture
async def async_client(app_lifespan):
    transport = ASGITransport(app=app_lifespan)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
```

---

## Summary Matrix

| Antipattern | Primary Impact | Failure Manifestation | Production Fix |
| :--- | :--- | :--- | :--- |
| **Drop/Create Schema** | 50x–100x Slower Suites | 30s+ runtime for 100 tests | Transactional rollback fixture (< 1ms/test) |
| **Starlette `TestClient`** | Thread-boundary isolation | Deadlocks & broken transaction sharing | Native `httpx.AsyncClient` with `ASGITransport` |
| **Standard `:memory:`** | Table Destruction | `OperationalError: no such table` | Shared cache URI + `poolclass=StaticPool` |
| **Background Commits** | State Pollution | Rolled back tests leak rows | Decouple side-effect workers via `dependency_overrides` |
| **`unittest.mock.patch`** | Global Namespace Mutation | Race conditions in parallel test workers | Scoped `app.dependency_overrides` |
| **Commit in Fixtures** | Broken Rollback | `UNIQUE constraint failed` on next test | Use `await session.flush()` instead of `commit()` |
| **Missing Lifespan** | Uninitialized State | `AttributeError` on `app.state` | Manage lifespan explicitly in session fixtures |
