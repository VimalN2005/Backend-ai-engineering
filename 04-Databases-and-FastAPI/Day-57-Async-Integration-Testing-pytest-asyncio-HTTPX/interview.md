# Senior & Staff Engineering Interview Q&A: Async Integration Testing

This document compiles the top 5 advanced architectural and engineering interview questions regarding asynchronous integration testing, database isolation fixtures, event loop lifecycles, and HTTP mock transports, designed for Senior and Staff Backend & AI Systems Engineers.

---

### Question 1: How does the Transactional Rollback fixture pattern work mechanically in SQLAlchemy 2.0 with nested savepoints (`begin_nested`), and how does it maintain isolation when application code calls `session.commit()`?

#### Conceptual Answer
The **Transactional Rollback Pattern** is the industry standard for fast, deterministic database testing. Instead of running expensive DDL (`CREATE TABLE` / `DROP TABLE`) between tests, the schema is created once per session. Each test runs inside an isolated database transaction that is rolled back during fixture teardown.

However, a major mechanical challenge arises when the application under test (e.g., an order checkout route) explicitly calls `await session.commit()`. If only a single outer transaction exists (`await conn.begin()`), the application's `commit()` commits the physical database connection, permanently persisting the test data and ruining rollback isolation for subsequent tests.

#### Mechanical Deep Dive: The Savepoint & Event Listener Loop
To solve this, modern test harnesses employ **Nested Transactions (SQL Savepoints)** combined with a SQLAlchemy event listener:

1. **Outer Connection Transaction**: The test fixture opens a database connection and begins an outer transaction:
   `trans = await connection.begin()` (issues SQL `BEGIN`).
2. **Inner Savepoint**: The fixture creates a nested transaction:
   `nested = await connection.begin_nested()` (issues SQL `SAVEPOINT sa_savepoint_1`).
3. **Session Binding**: An `AsyncSession` is bound to this connection.
4. **Event Listener for Savepoint Continuation**:
   When application code calls `await session.commit()`, SQLAlchemy releases the savepoint (`RELEASE SAVEPOINT sa_savepoint_1`), but the outer transaction remains active. To ensure subsequent operations within the same test are also isolated, an event hook listens for `after_transaction_end`:
   ```python
   @event.listens_for(session.sync_session, "after_transaction_end")
   def restart_savepoint(session, transaction):
       if connection.sync_connection and not connection.sync_connection.in_nested_transaction():
           connection.sync_connection.begin_nested()
   ```
5. **Teardown Rollback**: At test completion, the fixture executes:
   `await trans.rollback()` (issues SQL `ROLLBACK`).
   This discards the entire outer transaction, erasing all nested savepoints and committed changes instantaneously in `< 1 millisecond`.

---

### Question 2: Why does Starlette's `TestClient` cause deadlocks and context leakage in async integration suites, and why is HTTPX `ASGITransport` the modern standard?

#### Conceptual Answer
Starlette's `TestClient` was originally built to allow synchronous test code to execute against asynchronous Starlette and FastAPI applications. Under the hood, it uses an AnyIO portal (`anyio.from_thread.start_blocking_portal()`).

#### The Thread-Boundary Flaw
When `TestClient` executes a request:
1. It spawns a **separate OS background thread** dedicated to running an asynchronous event loop.
2. The route handler, middleware, and dependencies execute on that background thread.
3. The pytest test function executes on the **main thread**.

This thread separation breaks asynchronous integration testing in three distinct ways:

- **Database Connection / Transaction Sharing**: In-memory database engines (like SQLite `StaticPool`) or transaction-bound connections cannot be shared across threads. If the test fixture begins a transaction on Thread A, the route handler on Thread B cannot see the transaction or acquire connection locks, causing either a `no such table` error or an immediate deadlock.
- **`contextvars` Severance**: Python's `contextvars` (used for correlation IDs, tenant IDs, and tracing contexts) are bound to the coroutine task tree. Hopping across an AnyIO thread portal breaks context variable propagation unless explicitly serialized.
- **Event Loop Disconnect**: If an async fixture sets up an async lock (`asyncio.Lock()`) or queues an async background task, accessing it from the background thread triggers:
  `RuntimeError: Task <Task> got Future <Future> attached to a different loop.`

#### The Solution: Native `httpx.ASGITransport`
`httpx.AsyncClient(transport=ASGITransport(app=app))` does not spawn background threads. It invokes the ASGI application callable (`await app(scope, receive, send)`) directly on the **current test event loop**. The test and the application share the identical event loop, OS thread, memory space, and database connection.

---

### Question 3: How do you mock external microservices and third-party APIs without monkeypatching internal service classes?

#### Conceptual Answer
In enterprise microservice architectures, applications communicate with external services (Stripe, Twilio, OpenAI, GitHub). Mocking the internal service class using `unittest.mock.patch("app.services.stripe.charge")` is an architectural antipattern:
1. **Implementation Coupling**: Tests are tightly coupled to internal function names and method signatures. Renaming a method breaks tests even though the external API contract never changed.
2. **False Positives**: A mock cannot verify whether the application correctly serialized HTTP headers, path parameters, query strings, or JSON body payloads.
3. **Global Namespace Pollution**: `mock.patch` mutates module globals, causing flaky race conditions during parallel test execution (`pytest -n auto`).

#### The Solution: `httpx.MockTransport` at the HTTP Wire Boundary
Instead of mocking Python classes, mock the **outbound HTTP transport protocol**. HTTPX provides `MockTransport`, which allows intercepting outbound requests at the HTTP wire layer:

```python
import httpx

def stripe_mock_handler(request: httpx.Request) -> httpx.Response:
    # Verifies exact HTTP method, path, and payload structure
    assert request.url.path == "/v1/charges"
    assert request.headers["Authorization"].startswith("Bearer sk_test_")
    
    payload = json.loads(request.content)
    if payload["amount"] > 100000:
        return httpx.Response(status_code=400, json={"error": "Charge limit exceeded"})
    
    return httpx.Response(status_code=200, json={"id": "ch_mock_123", "paid": True})

# Inject mock transport into the app's outbound HTTP client dependency
mock_client = httpx.AsyncClient(
    transport=httpx.MockTransport(stripe_mock_handler),
    base_url="https://api.stripe.com"
)
app.dependency_overrides[get_stripe_client] = lambda: mock_client
```

This pattern tests the complete internal serialization logic, validates authentication headers, and allows simulating timeouts and connection drops deterministically.

---

### Question 4: How does `pytest-asyncio` manage the asyncio event loop lifecycle across test fixtures and scopes, and what changed in modern versions (v0.23+ and v1.0+)?

#### Conceptual Answer
The core challenge of running asynchronous tests in pytest is matching the lifecycle of the **asyncio event loop** with the lifecycle of **pytest fixtures**.

#### The Legacy Problem (< v0.23)
In earlier versions of `pytest-asyncio`:
- By default, `pytest-asyncio` created a **new event loop for every single test function**.
- If a developer created a session-scoped fixture (e.g., an expensive SQLAlchemy engine or a Redis client pool):
  ```python
  @pytest.fixture(scope="session")
  async def engine(): ...
  ```
  The `engine` was initialized on the event loop of Test 1. When Test 2 executed, `pytest-asyncio` destroyed the loop and created a new one! Attempting to use the `engine` on Test 2's loop threw:
  `RuntimeError: Task attached to a different loop.`

#### The Modern Architecture (v0.23+ and v1.0+)
Modern `pytest-asyncio` introduced explicit **loop scoping**:
1. **Explicit Fixture Scopes**: Fixtures specify their loop scope using `loop_scope="function"`, `"module"`, or `"session"`.
2. **`asyncio_mode = "auto"`**: Automatically converts all `async def test_*` functions into async test cases without needing `@pytest.mark.asyncio` decorators.
3. **Event Loop Policy Customization**: Developers can define a session-scoped event loop policy or configure `asyncio_default_fixture_loop_scope = "function"` in `pyproject.toml`.

For integration testing, the recommended pattern is:
- **Session Scope**: Initialize the database engine and compile ORM metadata once.
- **Function Scope**: Open isolated connections, transactions, and HTTP clients per test function.

---

### Question 5: How do you design and execute deterministic race-condition integration tests in Python without introducing flaky sleep timers?

#### Conceptual Answer
Testing concurrent state transitions (e.g., double-spend, inventory overselling, coupon redemption) is notoriously difficult. Relying on `time.sleep()` or `asyncio.sleep()` hoping requests will interleave at the right microsecond results in **flaky tests** that pass on developer laptops and fail in CI/CD pipelines.

#### Deterministic Concurrency Architecture
To build deterministic concurrency tests:

1. **In-Process ASGI Concurrency with `asyncio.gather`**:
   Because `httpx.ASGITransport` runs on the local event loop, spawning multiple HTTP requests using `asyncio.gather(*tasks)` dispatches all coroutines cooperatively.
   ```python
   tasks = [
       client.post(f"/api/v1/accounts/{acc_id}/withdraw", json={"amount": 100})
       for _ in range(10)
   ]
   responses = await asyncio.gather(*tasks)
   ```
2. **Database Concurrency Controls Under Test**:
   The endpoint under test must implement explicit concurrency controls:
   - **Pessimistic Row-Level Locking**: `SELECT ... FOR UPDATE` (or `select(Account).with_for_update()`) in PostgreSQL/MySQL.
   - **Optimistic Concurrency Control**: Checking `WHERE version = :expected_version` and asserting affected row count equals 1.
3. **Statistical Distribution Assertions**:
   Instead of asserting arbitrary request timing, assert the **invariant outcome distribution**:
   - If an account has a balance of $100 and 10 simultaneous requests attempt to withdraw $100:
     - Exactly **1 request must return HTTP 200**.
     - Exactly **9 requests must return HTTP 409 Conflict** (or HTTP 400 Insufficient Funds).
     - The final account balance in the database must be **exactly $0.00**, never negative.
