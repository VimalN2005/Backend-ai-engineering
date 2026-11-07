# Day 54: Advanced FastAPI Dependency Injection Practical Exercises

Mastering FastAPI's dependency injection and session management requires designing decoupled, leak-free, and composable architectures. Complete the following three senior-level engineering exercises. Verified reference solutions follow each prompt.

---

## Exercise 1: Multi-Tenant Schema-Isolated Session Factory

### Architectural Scenario
In an enterprise SaaS backend using PostgreSQL multi-tenancy, each enterprise tenant has their own isolated database schema (e.g., `tenant_acme`, `tenant_megacorp`).

You must implement an asynchronous database session dependency `get_tenant_db_session` that:
1. Resolves the tenant slug from an `X-Tenant-Slug` header.
2. Validates that the tenant is active and registered.
3. Obtains an `AsyncSession` and binds a schema translation mapping (`schema_translate_map={None: tenant_slug}`) using SQLAlchemy's execution options so all ORM operations automatically target the tenant's isolated schema.
4. Manages atomic commits and ensures guaranteed teardown via `finally`.

### Requirements
1. Implement `get_tenant_slug` header dependency.
2. Implement `get_tenant_db_session` yield generator dependency.
3. Reject missing or unauthorized tenant slugs with HTTP 401/403.
4. Guarantee that sessions are closed and returned to the connection pool on teardown.

### Verified Reference Solution

```python
from typing import Annotated, AsyncGenerator, Dict, Optional
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Mock tenant registry
REGISTERED_TENANTS: Dict[str, dict] = {
    "acme_corp": {"is_active": True, "schema_name": "tenant_acme_corp"},
    "globex": {"is_active": True, "schema_name": "tenant_globex"},
    "suspended_co": {"is_active": False, "schema_name": "tenant_suspended"},
}

async def get_tenant_slug(x_tenant_slug: Annotated[Optional[str], Header()] = None) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required 'X-Tenant-Slug' header.",
        )
    slug = x_tenant_slug.strip().lower()
    if slug not in REGISTERED_TENANTS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Tenant '{slug}' is not recognized.",
        )
    if not REGISTERED_TENANTS[slug]["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant '{slug}' account is suspended.",
        )
    return slug

async def get_tenant_db_session(
    tenant_slug: Annotated[str, Depends(get_tenant_slug)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(lambda: session_factory_provider)],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an AsyncSession dynamically configured with the tenant's schema mapping.
    """
    schema_name = REGISTERED_TENANTS[tenant_slug]["schema_name"]
    
    # Configure session with schema_translate_map
    async with session_factory() as session:
        # Dynamically set execution options for schema translation
        session.sync_session.connection(
            execution_options={"schema_translate_map": {None: schema_name}}
        )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

---

## Exercise 2: Parameterized Sliding-Window Rate Limiter Dependency

### Architectural Scenario
To protect public API endpoints from denial-of-service (DoS) and credential-stuffing attacks, you must implement a reusable, class-based **Rate Limiter Dependency** parameterized with request limits and time windows.

### Requirements
1. Design a class `RateLimiter` accepting `max_requests: int` and `window_seconds: int`.
2. Implement `__call__` extracting the client's IP address (or `X-Forwarded-For` header).
3. Track request timestamps in an in-memory sliding window or Redis mock.
4. If requests exceed `max_requests` in the window, raise HTTP `429 Too Many Requests` with a `Retry-After` header.

### Verified Reference Solution

```python
import time
from collections import defaultdict
from typing import Annotated, Dict, List
from fastapi import HTTPException, Request, Response, status

class SlidingWindowRateLimiter:
    """
    Class-based parameterized dependency for sliding-window rate limiting.
    Usage: Depends(SlidingWindowRateLimiter(max_requests=5, window_seconds=60))
    """
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # In-memory store: {ip_address: [timestamp, timestamp, ...]}
        self.history: Dict[str, List[float]] = defaultdict(list)

    def __call__(self, request: Request, response: Response) -> str:
        client_ip = request.headers.get("X-Forwarded-For") or (request.client.host if request.client else "127.0.0.1")
        now = time.time()
        window_start = now - self.window_seconds

        # Prune timestamps older than window_seconds
        self.history[client_ip] = [t for t in self.history[client_ip] if t > window_start]

        if len(self.history[client_ip]) >= self.max_requests:
            oldest_request = self.history[client_ip][0]
            retry_after = int(oldest_request + self.window_seconds - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Slow down your requests.",
                headers={"Retry-After": str(retry_after), "X-RateLimit-Limit": str(self.max_requests)},
            )

        self.history[client_ip].append(now)
        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(self.max_requests - len(self.history[client_ip]))
        return client_ip

# Pre-configured instances for different route sensitivities
rate_limit_standard = SlidingWindowRateLimiter(max_requests=100, window_seconds=60)
rate_limit_strict_auth = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
```

---

## Exercise 3: Async Unit of Work (UoW) Dependency with Nested Savepoints

### Architectural Scenario
Complex enterprise operations (e.g. creating an order, updating warehouse inventory, and logging an audit trail) require transactional coordination across multiple repositories.

Design an **Async Unit of Work (UoW)** dependency providing:
1. Injected repositories sharing the exact same `AsyncSession`.
2. An atomic `async with uow:` boundary.
3. A `savepoint()` context manager for isolated sub-operations that can fail without rolling back the entire transaction.

### Requirements
1. Implement `AsyncUnitOfWork` class.
2. Expose `get_uow` as a yield generator dependency.
3. Test that a failure inside a `savepoint()` rolls back only the sub-operation while allowing the primary transaction to commit successfully.

### Verified Reference Solution

```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

class AsyncUnitOfWork:
    """
    Coordinates multi-repository operations within a single database transaction.
    """
    def __init__(self, session: AsyncSession):
        self.session = session
        # Repositories share the identical session instance
        self.orders = OrderRepository(session)
        self.inventory = InventoryRepository(session)
        self.audit = AuditRepository(session)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    @asynccontextmanager
    async def savepoint(self):
        """
        Creates a database SAVEPOINT.
        Permits isolated operations to fail without aborting the parent transaction.
        """
        async with self.session.begin_nested() as sp:
            yield sp

async def get_unit_of_work(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
) -> AsyncGenerator[AsyncUnitOfWork, None]:
    async with session_factory() as session:
        uow = AsyncUnitOfWork(session)
        try:
            yield uow
            await uow.commit()
        except Exception:
            await uow.rollback()
            raise
        finally:
            await session.close()
```

---
*Completing these exercises ensures production-level mastery of multi-tenant session binding, security dependencies, and atomic Unit of Work patterns.*
