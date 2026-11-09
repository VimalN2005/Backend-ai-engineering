# Day 57: Async Integration Testing with `pytest-asyncio` & HTTPX

A production-grade architectural guide to building fast, deterministic, and isolated asynchronous integration test suites for FastAPI applications using `pytest-asyncio`, HTTPX `ASGITransport`, SQLAlchemy transactional rollback fixtures, and dependency overrides.

---

## 1. The Modern Testing Pyramid in Asynchronous Systems

In high-concurrency asynchronous systems, unit testing isolated functions provides fast feedback but fails to detect the most catastrophic production failure modes:
- **Asynchronous deadlocks** and event loop blocking.
- **ASGI middleware header corruption** and context variable leakage.
- **SQLAlchemy transaction isolation leaks** and N+1 query explosions.
- **Database foreign key and check constraint violations**.
- **Pydantic deserialization mismatches** on HTTP boundary payloads.

```
       / \
      / E2E \         End-to-End Tests (Playwright / Cypress) - Slow, fragile, high maintenance
     /-------\
    /  Async  \       Async Integration Tests (pytest-asyncio + HTTPX + DB Rollback)
   /Integration\      - High ROI: Exercises ASGI stack, DI, routing, ORM, and SQL constraints
  /-------------\
 /   Unit Tests  \    Fast, pure in-memory unit tests (services, algorithms, calculations)
/-----------------\
```

Async integration testing tests the **full application stack**—from incoming HTTP requests, through the ASGI middleware onion, dependency injection DAG, route handlers, down to the actual database engine—without binding to physical network ports.

---

## 2. `pytest-asyncio` Internals & Event Loop Scoping

`pytest-asyncio` is the standard test runner extension for asynchronous Python. Understanding its event loop lifecycle is crucial for preventing cross-test state pollution.

### The Evolution of Loop Scoping
- **Legacy `pytest-asyncio` (< v0.23)**: Created an event loop per test function by default. If a developer created a session-scoped database engine or Redis pool, subsequent test functions would run in a *different* event loop, raising:
  `RuntimeError: Task <Task> got Future <Future> attached to a different loop.`
- **Modern `pytest-asyncio` (v0.23+ and v1.0+)**:
  Enforces explicit loop scoping via `loop_scope` on fixtures and markers (`@pytest.mark.asyncio(loop_scope="function")` or `"session"`).
  With `asyncio_mode = "auto"` in `pyproject.toml`, every `async def test_*` function is automatically scheduled on an isolated event loop.

### Recommended `pyproject.toml` Configuration
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
filterwarnings = [
    "ignore::DeprecationWarning",
    "ignore::PendingDeprecationWarning",
]
```

---

## 3. HTTPX `ASGITransport` vs. Starlette `TestClient`

A common pitfall is using Starlette's `TestClient` in asynchronous integration test suites.

| Architectural Dimension | Starlette `TestClient` | HTTPX `AsyncClient` + `ASGITransport` |
| :--- | :--- | :--- |
| **Execution Paradigm** | Synchronous wrapper using AnyIO portals | Native asynchronous coroutine execution |
| **Thread Model** | Executes ASGI application on a **separate background thread** | Executes directly on the **test's current event loop** |
| **`contextvars` Propagation** | Severed across the thread boundary | Preserved natively within the async task context |
| **Database Transaction Sharing** | **Impossible**: Cannot share a connection or savepoint between test and app thread | **Seamless**: Test and app share the identical async connection & transaction |
| **Deadlock Risk** | High: Nested async database locks deadlock across threads | Zero: Fully cooperative coroutine scheduling on single loop |

### The Production ASGI Test Client Pattern
```python
import httpx
from httpx import ASGITransport

async with httpx.AsyncClient(
    transport=ASGITransport(app=app),
    base_url="http://testserver",
    headers={"Accept": "application/json"}
) as client:
    response = await client.get("/api/v1/health")
```

---

## 4. FastAPI `app.dependency_overrides` Architecture

FastAPI provides the most elegant test-doubling mechanism among Python web frameworks: `app.dependency_overrides`.

### Why `dependency_overrides` Trumps `unittest.mock.patch`
1. **No Monkeypatching**: `mock.patch` mutates module-level globals, causing race conditions in concurrent test runs and leaking state if teardown fails.
2. **Type Safety**: Replacement callables adhere to the exact same signature and Pydantic return schemas as production dependencies.
3. **Targeted Scoping**: You can override a single low-level dependency (e.g., `get_db_session` or `get_current_user`) while preserving the rest of the 10-node dependency DAG.

```python
# Clean dependency override pattern in test fixture
@pytest.fixture
def override_auth_user(app: FastAPI):
    mock_user = User(id="usr_test_123", email="tester@enterprise.internal", is_admin=True)
    app.dependency_overrides[get_current_user] = lambda: mock_user
    yield mock_user
    # Mandatory cleanup in fixture teardown
    app.dependency_overrides.pop(get_current_user, None)
```

---

## 5. Database Test Isolation: The Transactional Rollback Pattern

The speed and reliability of your test suite depends directly on how the database state is reset between tests.

```
Traditional Approach (Slow - 100ms/test):
[Test 1] -> CREATE TABLES -> INSERT DATA -> DROP TABLES
[Test 2] -> CREATE TABLES -> INSERT DATA -> DROP TABLES

Transactional Rollback Approach (Ultra-Fast - 1ms/test):
[Session Setup] -> CREATE ALL TABLES ONCE (Engine / StaticPool)
      |
      +---> [Test 1] -> BEGIN TRANSACTION -> SAVEPOINT -> Route Operations -> ROLLBACK TO SAVEPOINT
      |
      +---> [Test 2] -> BEGIN TRANSACTION -> SAVEPOINT -> Route Operations -> ROLLBACK TO SAVEPOINT
```

### The Nested Savepoint (`begin_nested`) Pattern
1. Create database schema **once** per test session.
2. For each test, open an `AsyncConnection` and start an outer transaction: `await conn.begin()`.
3. Open a nested transaction (SQL `SAVEPOINT`): `await conn.begin_nested()`.
4. Bind an `AsyncSession` to this connection with `join_transaction_mode="create_savepoint"`.
5. Pass this session to the route handler via `dependency_overrides[get_db_session]`.
6. When the test finishes, **rollback** the outer transaction: `await trans.rollback()`.
7. **Result**: Zero disk I/O, zero dirty data left for subsequent tests, and execution times under **2 milliseconds per test**.

---

## 6. SQLite `aiosqlite` In-Memory Concurrency & Shared Cache

When running tests against in-memory SQLite, developers often encounter mysterious missing table errors:
`sqlite3.OperationalError: no such table: users`

### The Root Cause
A standard in-memory connection (`sqlite+aiosqlite:///:memory:`) creates a database that exists **exclusively for that single connection**. When SQLAlchemy closes or opens a connection in its pool, the database is instantly destroyed!

### The Production In-Memory URI
Use SQLite's **Shared In-Memory Cache** combined with SQLAlchemy's `StaticPool`:

```python
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared&uri=true"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # Maintains exactly one connection alive across all sessions
)
```

---

## 7. Mocking External I/O with HTTPX `MockTransport`

Enterprise microservices communicate with external third-party services (Stripe, Twilio, OpenAI, GitHub). Mocking the internal client class (e.g., `mock.patch("app.services.stripe.charge")`) couples tests to internal implementation details.

### The `MockTransport` Pattern
HTTPX provides `MockTransport`, allowing you to mock third-party services at the **HTTP protocol boundary**:

```python
import httpx

def stripe_mock_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/charges" and request.method == "POST":
        return httpx.Response(
            status_code=200,
            json={"id": "ch_mock_123", "status": "succeeded", "amount": 5000}
        )
    return httpx.Response(status_code=404, json={"error": "Not Found"})

# Inject mock transport into the app's outbound HTTP client
mock_transport = httpx.MockTransport(stripe_mock_handler)
app.dependency_overrides[get_stripe_client] = lambda: httpx.AsyncClient(transport=mock_transport)
```

---

## 8. Lifespan Management in Async Integration Tests

When you pass an application directly to `ASGITransport(app=app)`, Starlette does **not** trigger application lifespan events (`@asynccontextmanager async def lifespan(app: FastAPI)`).

If your application initializes database connection pools, Redis clients, or ML model caches inside `lifespan`, those resources will be `None` during testing!

### Managing Lifespans with `LifespanManager` or `AsyncClient`
In modern Starlette and HTTPX, wrap the test client in the ASGI lifespan context:

```python
from contextlib import asynccontextmanager
import httpx
from httpx import ASGITransport

@pytest.fixture
async def async_client(app: FastAPI):
    # Trigger startup and shutdown lifespan events explicitly
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client
```

---

## 9. Authentication & Authorization Bypass in Tests

Testing protected routes requires authenticated user contexts. Avoid running full OAuth2 password hashing routines for every single test, as bcrypt takes ~300ms per verification.

### Enterprise Test Authentication Patterns
1. **Dependency Override Pattern (Fastest - 0ms overhead)**:
   ```python
   app.dependency_overrides[get_current_active_user] = lambda: test_user
   ```
2. **Pre-Minted JWT Fixture Pattern (Tests JWT decoding middleware)**:
   Generate pre-signed JWT tokens using a fixed test secret key, bypassing password checks while testing signature parsing, expiration, and scope enforcement:
   ```python
   @pytest.fixture
   def admin_token() -> str:
       return create_jwt_token(sub="admin_user_1", scopes=["admin:*"])
   ```

---

## 10. Testing Streaming Responses & Server-Sent Events (SSE)

Testing `StreamingResponse` requires streaming client methods rather than buffered `.json()` access:

```python
async with async_client.stream("GET", "/api/v1/ai/stream?prompt=Hello") as response:
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    
    tokens = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            tokens.append(line.replace("data: ", ""))
            
    assert len(tokens) > 0
    assert tokens[-1] == "[DONE]"
```

---

## 11. Testing `BackgroundTasks` in Integration Scenarios

FastAPI's `BackgroundTasks` execute after the HTTP response has been generated. Because `ASGITransport` executes on the same event loop, background tasks are awaited before `client.post()` returns.

### Verification Pattern
```python
async def test_user_registration_triggers_audit_task(async_client, test_db_session):
    response = await async_client.post("/api/v1/users", json={"email": "alice@test.com"})
    assert response.status_code == 201
    
    # Query database to confirm background task side effect was written
    result = await test_db_session.execute(select(AuditLog).where(AuditLog.action == "USER_REGISTERED"))
    audit_record = result.scalar_one_or_none()
    assert audit_record is not None
```

---

## 12. Concurrent Request & Race Condition Testing

Async applications are prone to race conditions (e.g., double-spend, inventory oversell). Use `asyncio.gather()` inside an integration test to fire simultaneous requests against the same resource:

```python
async def test_prevent_double_spend_race_condition(async_client, seed_account):
    # Fire 5 concurrent withdrawal requests for an account with balance = 100
    tasks = [
        async_client.post(f"/api/v1/accounts/{seed_account.id}/withdraw", json={"amount": 100})
        for _ in range(5)
    ]
    responses = await asyncio.gather(*tasks)
    
    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1, "Exactly one withdrawal must succeed"
    assert status_codes.count(409) == 4, "Concurrent race attempts must be rejected with HTTP 409 Conflict"
```

---

## 13. Database Seed Fixtures & Factory Patterns

Avoid hardcoded SQL insert scripts across test files. Use composed pytest fixtures and async model factories:

```python
@pytest.fixture
async def seed_tenant(db_session: AsyncSession) -> Tenant:
    tenant = Tenant(name="Acme Corp", slug="acme", plan="enterprise")
    db_session.add(tenant)
    await db_session.flush()  # Assigns primary key without committing outer transaction
    return tenant

@pytest.fixture
async def seed_user(db_session: AsyncSession, seed_tenant: Tenant) -> User:
    user = User(tenant_id=seed_tenant.id, email="lead@acme.com", role="admin")
    db_session.add(user)
    await db_session.flush()
    return user
```

---

## 14. CI/CD Performance & Parallelism (`pytest-xdist`)

To achieve sub-minute test suite execution in CI/CD pipelines:
1. **Parallel Execution**: Use `pytest -n auto` to distribute test files across available CPU cores.
2. **Database Isolation per Worker**: When testing against a real PostgreSQL instance in CI, use `pytest-xdist` worker IDs (`worker_id = os.environ.get("PYTEST_XDIST_WORKER")`) to provision independent databases: `test_db_gw0`, `test_db_gw1`.
3. **Fail-Fast**: Use `pytest -x` to stop execution on the first failure.

---

## 15. Production Readiness & Quality Checklist

- [x] **Zero Port Binding**: All integration tests run over `httpx.ASGITransport` without binding to physical sockets or local ports.
- [x] **Transactional Rollback**: 100% of database tests utilize `begin_nested()` savepoints, guaranteeing clean state with zero disk recreation overhead.
- [x] **No Thread Leaks**: Uses pure `httpx.AsyncClient` on the native event loop instead of `TestClient` AnyIO portals.
- [x] **Mocking at Boundaries**: Outbound HTTP I/O is intercepted via `httpx.MockTransport` rather than monkeypatching internal service classes.
- [x] **Deterministic Fixtures**: Fixtures flush (`await session.flush()`) instead of committing (`await session.commit()`) to keep records inside the test savepoint.
- [x] **Race Condition Coverage**: Critical state-transition endpoints are tested with concurrent `asyncio.gather()` bursts.
