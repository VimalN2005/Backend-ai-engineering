# Day 54: FastAPI Dependency Injection (`Depends`) & Session Management

Welcome to Day 54 of the Enterprise Backend & AI Engineering curriculum. Today, we master the architectural backbone of scalable FastAPI applications: the **Dependency Injection (DI) system** and **Session Management**.

In enterprise architectures, applications cannot hardcode database connections, authentication providers, or external service clients directly inside route handlers. Doing so produces tightly coupled, un-testable, and fragile codebases prone to connection leaks and security vulnerabilities. FastAPI's Dependency Injection system—centered around the **`Depends`** callable protocol—provides an elegant, type-safe, and asynchronous Inversion of Control (IoC) engine that resolves complex dependency graphs, enforces transaction boundaries, and enables seamless test mocking.

This guide provides a comprehensive 15-point architectural deep dive into dependency graphs, yield dependencies, session lifecycles, and production resource management.

---

## 1. The Philosophy of Inversion of Control (IoC) in FastAPI

In traditional synchronous web frameworks (or naive codebases), route handlers instantiate their own dependencies directly:

```python
# TIGHTLY COUPLED (ANTIPATTERN)
@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    # Hardcoded session creation & service instantiation
    engine = create_async_engine("postgresql+asyncpg://...")
    session = AsyncSession(engine)
    repo = OrderRepository(session)
    order = await repo.find_by_id(order_id)
    await session.close()
    return order
```

**The Architectural Failure**:
1. **Zero Testability**: Writing a unit or integration test requires mocking low-level socket connections or patching global imports via `unittest.mock.patch`.
2. **Resource Leaks**: If `repo.find_by_id()` raises an unhandled exception, `session.close()` is skipped, leaking database connections until the server runs out of file descriptors.
3. **No Code Reuse**: Authentication, database connection pooling, tenant resolution, and rate limiting must be manually copy-pasted across hundreds of routes.

### The Inversion of Control (IoC) Solution
With FastAPI's Dependency Injection, the framework assumes responsibility for instantiating dependencies, providing them to the route handler, and cleaning them up when the HTTP request terminates:

```python
# DECOUPLED & TYPE-SAFE (ENTERPRISE STANDARD)
@app.get("/orders/{order_id}")
async def get_order(
    order_id: int,
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    return await order_service.get_order_by_id(order_id)
```

FastAPI builds a Directed Acyclic Graph (DAG) of all required components, executes them in topological order, injects the resolved instances into function arguments, and guarantees post-request cleanup.

---

## 2. The `Depends` Callable Protocol

FastAPI's `Depends` accepts any Python **callable**:
- An `async def` coroutine function.
- A standard `def` synchronous function.
- A Python class (which invokes `__init__` or `__new__`).
- An instance of a class implementing the `__call__` dunder method.

```python
from fastapi import Depends
from typing import Annotated

# 1. Async function dependency
async def get_query_param(q: str = "") -> str:
    return q.strip().lower()

# 2. Class-based dependency
class CommonPagination:
    def __init__(self, page: int = 1, page_size: int = 20):
        self.skip = (page - 1) * page_size
        self.limit = page_size

@app.get("/items")
async def list_items(
    q: Annotated[str, Depends(get_query_param)],
    pagination: Annotated[CommonPagination, Depends(CommonPagination)],
):
    return {"query": q, "skip": pagination.skip, "limit": pagination.limit}
```

*Note*: When a class is passed to `Depends` (e.g. `Depends(CommonPagination)`), FastAPI inspects its `__init__` method, extracts its parameters from the HTTP request (Query/Path/Header/Body), and instantiates the class automatically. FastAPI even allows the shorthand `Depends()` when type-annotated with the class: `pagination: Annotated[CommonPagination, Depends()]`.

---

## 3. Sub-Dependencies & Hierarchical Graph Resolution (DAG)

Dependencies can depend on other dependencies, forming a hierarchical **Directed Acyclic Graph (DAG)**:

```
                  [ Incoming HTTP Request ]
                              |
                +-------------+-------------+
                |                           |
                v                           v
     [ get_api_key_header ]        [ get_db_engine ]
                |                           |
                v                           v
     [ authenticate_tenant ]       [ get_db_session ]
                \                           /
                 \                         /
                  +-----------+-----------+
                              |
                              v
                  [ get_order_repository ]
                              |
                              v
                   [ get_order_service ]
                              |
                              v
                 [ Route: /api/v1/orders ]
```

### Resolution Order
1. FastAPI parses the endpoint signature and builds the dependency tree.
2. Independent root nodes (such as extracting headers or query parameters) are executed first.
3. Intermediate nodes receive the outputs of parent dependencies as function arguments.
4. If any dependency in the chain raises an `HTTPException` (e.g. invalid API key), the chain halts immediately, child dependencies are skipped, and the error response is returned.

---

## 4. Yield Dependencies (`yield`) & Resource Management

For dependencies that require setup before request execution and teardown after request execution, FastAPI uses **generator functions with `yield`**:

```python
from typing import AsyncGenerator

async def get_resource() -> AsyncGenerator[Resource, None]:
    # 1. SETUP PHASE: Runs before route handler executes
    resource = await acquire_resource()
    try:
        yield resource  # Injected into the route handler
    finally:
        # 2. TEARDOWN PHASE: Guaranteed execution after response is sent
        await resource.release()
```

### Execution Sequencing
1. Client sends request $\implies$ FastAPI starts resolving dependencies.
2. Execution enters `get_resource()` and runs until it reaches `yield`.
3. The yielded value (`resource`) is injected into the endpoint argument.
4. The endpoint handler executes and returns a response.
5. The HTTP response is serialized and transmitted back to the client over the socket.
6. Execution resumes immediately after the `yield` statement in `get_resource()` to execute cleanup logic (e.g., in a `finally` block).

---

## 5. Database Session Lifecycle Management (`get_db`)

The canonical enterprise pattern for managing SQLAlchemy asynchronous sessions relies on yield dependencies:

```python
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Session factory configured at application startup
async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Per-request database session provider.
    Guarantees atomic transaction boundaries and zero connection leaks.
    """
    async with async_session_factory() as session:
        try:
            yield session
            # If the route handler completed without error, commit the transaction
            await session.commit()
        except Exception:
            # If an unhandled exception occurred, roll back pending writes
            await session.rollback()
            raise
        finally:
            # Close session and return connection to pool
            await session.close()
```

### Why Session Scoping Must Be Strictly Per-Request
- **Concurrency Isolation**: Sharing a single `AsyncSession` across concurrent asyncio tasks causes race conditions and corrupts transaction state.
- **Connection Conservation**: Opening sessions on-demand and closing them in `finally` blocks prevents connection starvation in the database connection pool.

---

## 6. Dependency Caching & the `use_cache` Flag

By default, FastAPI caches the result of a dependency within the scope of a single HTTP request (`use_cache=True`):

```python
@app.get("/dashboard")
async def get_dashboard(
    user: Annotated[User, Depends(get_current_user)],
    billing: Annotated[BillingService, Depends(get_billing_service)], # also calls get_current_user!
    audit: Annotated[AuditLogger, Depends(get_audit_logger)],         # also calls get_current_user!
):
    ...
```

### How `use_cache=True` Operates
1. In the route above, `get_current_user` is required by three distinct components.
2. Without caching, `get_current_user` would execute three separate times, querying the database and validating JWT tokens three times!
3. Because `use_cache=True` by default, FastAPI executes `get_current_user` **once** for the request, caches the returned `User` in an internal request-scoped lookup dictionary, and reuses the exact same instance for `billing` and `audit`.

### When to Set `use_cache=False`
```python
async def generate_transaction_id() -> str:
    return str(uuid.uuid4())

@app.post("/batch-transfer")
async def transfer(
    tx_id_1: Annotated[str, Depends(generate_transaction_id, use_cache=False)],
    tx_id_2: Annotated[str, Depends(generate_transaction_id, use_cache=False)],
):
    # Setting use_cache=False ensures generate_transaction_id() executes freshly each time!
    assert tx_id_1 != tx_id_2
```
Use `use_cache=False` when:
- Each injection site requires an independent, isolated instance (e.g. separate transaction savepoints).
- The dependency generates dynamic, non-idempotent values (e.g. unique idempotency tokens, fresh nonces).

---

## 7. Global, Router-Level & Endpoint-Level Dependencies

FastAPI allows scoping dependencies at three distinct levels:

### A. Endpoint-Level Dependencies
Applied directly to an individual route decorator:
```python
@app.get("/secure-data", dependencies=[Depends(verify_internal_network)])
async def get_secure_data():
    return {"status": "ok"}
```
*Note*: When passed to `dependencies=[Depends(...)]`, the dependency executes for side-effects (e.g. raising `403 Forbidden` if validation fails), and its return value is discarded.

### B. Router-Level Dependencies
Applied to all routes grouped inside an `APIRouter`:
```python
admin_router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin_role), Depends(audit_admin_access)],
)
```

### C. Application-Level (Global) Dependencies
Applied unconditionally to **every single route** across the entire application:
```python
app = FastAPI(dependencies=[Depends(enforce_rate_limit), Depends(verify_tenant_header)])
```

---

## 8. Class-Based Dependencies & Parameterized Providers

Class-based dependencies allow encapsulating state and parameterizing dependency behavior:

```python
class RoleChecker:
    """
    Reusable authorization dependency parameterized with required roles.
    """
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User role '{user.role}' lacks required permissions. Required: {self.allowed_roles}",
            )
        return user

# Instantiate parameterized dependencies
require_admin = RoleChecker(allowed_roles=["ADMIN", "SUPERUSER"])
require_billing = RoleChecker(allowed_roles=["ADMIN", "BILLING_MANAGER"])

@app.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: str,
    current_admin: Annotated[User, Depends(require_admin)],
):
    return {"status": f"Tenant {tenant_id} deleted by {current_admin.email}"}
```

---

## 9. Modern PEP 593 `typing.Annotated` Dependency Syntax

FastAPI 0.95+ strongly standardizes on `typing.Annotated`:

```python
# Modern Idiomatic Syntax (PEP 593)
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]

@app.get("/me")
async def get_current_profile(user: CurrentUser, db: DbSession):
    ...
```

### Why `Annotated` is Mandatory in Modern Backends
1. **Clean Function Signatures**: Route signatures become expressive domain statements (`user: CurrentUser`) rather than verbose framework syntax (`user: User = Depends(get_current_user)`).
2. **Static Typing & Mypy**: Static type checkers verify that `user` is an instance of `User`, not a default `Depends` object.
3. **Pure Unit Testing**: Route handlers can be imported and executed in plain unit tests by simply passing Python objects:
   ```python
   # In unit tests: zero framework mocking required!
   await get_current_profile(user=mock_user, db=mock_session)
   ```

---

## 10. The Service & Repository Pattern via Dependency Injection

Enterprise backends separate concerns by isolating data access from business orchestration:

```
[ FastAPI Controller ] (Extracts parameters, HTTP status, response models)
          |
       Depends(get_order_service)
          v
[ OrderService ]       (Business validation, tax calculations, payment processing)
          |
       Depends(get_order_repository)
          v
[ OrderRepository ]    (SQLAlchemy SQL queries, data hydration)
          |
       Depends(get_db_session)
          v
[ AsyncSession ]       (Connection pool socket & transaction boundaries)
```

```python
class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, order_id: int) -> Optional[Order]:
        return await self.session.get(Order, order_id)

class OrderService:
    def __init__(self, repo: OrderRepository):
        self.repo = repo

    async def cancel_order(self, order_id: int) -> Order:
        order = await self.repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        order.status = "CANCELLED"
        return order

# Dependency Providers
def get_order_repository(db: DbSession) -> OrderRepository:
    return OrderRepository(db)

def get_order_service(repo: Annotated[OrderRepository, Depends(get_order_repository)]) -> OrderService:
    return OrderService(repo)
```

---

## 11. Security & Authentication Dependencies

FastAPI includes built-in security utilities (`OAuth2PasswordBearer`, `HTTPBearer`, `APIKeyHeader`) that function as standard dependencies:

```python
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

async def verify_api_key(
    api_key: Annotated[str, Depends(api_key_header)],
    db: DbSession,
) -> Tenant:
    tenant = await db.query_tenant_by_api_key(api_key)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API Key.",
        )
    return tenant
```

---

## 12. Exception Handling & Teardown Execution in Yield Dependencies

A critical concern in production is whether yield teardown code executes when exceptions are thrown:

```python
async def get_managed_session():
    print("[SETUP] Opening database connection...")
    session = await pool.acquire()
    try:
        yield session
        print("[COMMIT] Committing transaction...")
        await session.commit()
    except Exception as e:
        print(f"[ROLLBACK] Caught exception: {e}. Rolling back...")
        await session.rollback()
        raise  # Re-raise so FastAPI's exception handlers can format the response
    finally:
        print("[TEARDOWN] Releasing connection back to pool...")
        await pool.release(session)
```

### Starlette Exception Boundary Interaction
1. If the route handler raises an `HTTPException(404, "Not Found")`, FastAPI propagates the exception down through the yield dependency context.
2. The `except Exception` block in the dependency intercepts the error, rolls back pending changes, and re-raises.
3. The `finally` block **always executes**, guaranteeing that the database connection is returned to the pool.
4. FastAPI's exception middleware intercepts the re-raised `HTTPException` and serializes the 404 JSON response.

---

## 13. Dependency Overrides for Hermetic Integration Testing

FastAPI provides the `app.dependency_overrides` dictionary, enabling instantaneous swapping of real infrastructure with test doubles:

```python
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.fixture
def test_client(app, mock_db_session):
    # Override production database dependency with in-memory SQLite or mock
    app.dependency_overrides[get_db_session] = lambda: mock_db_session
    
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    
    # Clean up overrides after test finishes
    app.dependency_overrides.clear()
```

### Why `dependency_overrides` Beats Mock Patching
- **No Global Side-Effects**: Overrides only affect the targeted `FastAPI` instance.
- **Refactoring Resilience**: If function internal variable names change, `dependency_overrides` does not break (unlike fragile `unittest.mock.patch("app.routes.db")`).

---

## 14. State Sharing: `Request.state` vs Dependency Injection

| Feature | `Request.state` | Dependency Injection (`Depends`) |
| :--- | :--- | :--- |
| **Typing** | Untyped dictionary-like object (`request.state.tenant_id`) | Fully type-safe via PEP 484/593 |
| **Lifecycle** | Set in Starlette Middleware | Resolved per-endpoint on-demand |
| **Testability** | Requires mocking the raw Starlette `Request` | Replaced cleanly via `app.dependency_overrides` |
| **Best Used For** | Transport-level metadata (request start time, raw correlation IDs) | Domain logic (database sessions, repositories, authenticated users) |

---

## 15. Production Anti-Leak Checklist & Pool Sizing

1. **Always Use `try...finally` in Yield Dependencies**: Never write a yield dependency without a `finally` block guarding resource closure.
2. **Explicit Rollbacks**: Always catch exceptions in session dependencies to issue `await session.rollback()` before releasing connections.
3. **Connection Pool Pre-Ping**: Ensure `pool_pre_ping=True` is enabled so that stale connections dropped by firewalls are recycled before being yielded.
4. **Pool Sizing Calculation**:
   $$\text{QueuePool Size} = (\text{Worker Processes} \times \text{Max Expected Concurrent Requests Per Worker}) + \text{Overflow}$$
5. **Zero Leaks Verification**: Track active vs checked-out pool connections using Prometheus metrics or SQLAlchemy pool event listeners (`checkout`, `checkin`).

---
*Next Step: Explore `example_01.py` for a fully functional enterprise dependency injection pipeline, and `example_02.py` for DAG resolution performance and connection leak diagnostic harnesses.*
