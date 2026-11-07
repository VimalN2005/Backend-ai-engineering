# Day 54: Critical FastAPI Dependency Injection & Session Management Antipatterns

This document explores the top 7 architectural mistakes engineers make when managing dependencies, database sessions, and resource lifecycles with **FastAPI** and **SQLAlchemy**. Each antipattern includes its underlying failure mechanism, bad code vs remediated code, and production rules.

---

## 1. Sharing a Single Global `AsyncSession` Across Concurrent Requests

### Failure Mechanism
In SQLAlchemy, `AsyncSession` is **not thread-safe or task-safe**. It maintains a stateful in-memory **Identity Map** and manages an active transactional context.

If a developer instantiates a single global `AsyncSession` and injects it across all incoming requests:
- Request A begins a transaction and adds an uncommitted record.
- Request B queries the database concurrently and sees dirty, uncommitted data from Request A.
- If Request A rolls back, it corrupts Request B's transaction state, raising:
  ```text
  InvalidRequestError: Can't operate on closed transaction inside concurrent task.
  ```

### Bad Practice
```python
# CATASTROPHIC GLOBAL SESSION: Corrupts data across concurrent asyncio tasks!
engine = create_async_engine(DATABASE_URL)
global_session = AsyncSession(engine)

@app.get("/users")
async def list_users():
    # Multiple concurrent requests share the exact same session instance!
    stmt = select(User)
    result = await global_session.execute(stmt)
    return result.scalars().all()
```

### Remediated Practice
```python
# Always provide fresh, isolated sessions per HTTP request via a yield dependency!
async_session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session  # Isolated per request, closed on exit

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

@app.get("/users")
async def list_users(session: DbSession):
    stmt = select(User)
    result = await session.execute(stmt)
    return result.scalars().all()
```

---

## 2. Omitting `try...finally` in Yield Dependencies (Connection Leak Trap)

### Failure Mechanism
If an error occurs either inside the route handler or inside child dependencies, execution jumps directly to the exception handler.

If a yield dependency acquires a resource (such as a database socket or Redis connection) without enclosing the teardown in a `finally` block, **the teardown code is skipped**. The database connection remains permanently checked out from the pool until the pool is exhausted, causing the entire API to hang!

### Bad Practice
```python
async def get_db():
    session = await pool.acquire()
    yield session
    # DANGEROUS: If the endpoint raises an exception, this line is NEVER reached!
    await pool.release(session)
```

### Remediated Practice
```python
async def get_db():
    session = await pool.acquire()
    try:
        yield session
    finally:
        # GUARANTEED: Executes 100% of the time, even during 500 server crashes
        await pool.release(session)
```

---

## 3. Swallowing Exceptions in Yield Dependencies Without Re-raising

### Failure Mechanism
When catching exceptions inside a yield dependency (e.g. to trigger a database `rollback`), developers sometimes forget to re-raise the exception (`raise`).

By swallowing the exception, FastAPI assumes the dependency resolved successfully. The custom exception handler (such as an `HTTPException` handler or validation handler) is bypassed, and the client receives a confusing HTTP `200 OK` with a `null` body instead of an informative error envelope!

### Bad Practice
```python
async def get_db():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            # BUG: Exception swallowed! FastAPI never knows an error occurred!
```

### Remediated Practice
```python
async def get_db():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise  # MUST re-raise so Starlette/FastAPI handles the error response!
```

---

## 4. Passing Database Sessions to `BackgroundTasks`

### Failure Mechanism
FastAPI `BackgroundTasks` are executed **after** the HTTP response has been sent to the client.

However, yield dependencies execute their teardown code **immediately after the response is sent**. If an injected `session` is passed to a background task:
1. The route finishes and returns HTTP 200.
2. The `get_db()` dependency runs its `finally` block and **closes the database session**.
3. The background task attempts to read an attribute on an entity or execute a query on the closed session, crashing with:
   ```text
   DetachedInstanceError: Parent instance is not bound to a Session
   ```

### Bad Practice
```python
@app.post("/orders")
async def create_order(
    order_data: CreateOrderDTO,
    background_tasks: BackgroundTasks,
    session: DbSession,
):
    order = await create_order_in_db(session, order_data)
    # BUG: Passing session (or ORM model bound to session) to background task!
    background_tasks.add_task(async_generate_invoice, session, order)
    return {"status": "enqueued"}
```

### Remediated Practice
```python
@app.post("/orders")
async def create_order(
    order_data: CreateOrderDTO,
    background_tasks: BackgroundTasks,
    session: DbSession,
):
    order = await create_order_in_db(session, order_data)
    # Pass primitive IDs to background tasks!
    background_tasks.add_task(async_generate_invoice, order.id)
    return {"status": "enqueued"}

async def async_generate_invoice(order_id: int):
    # Background task opens and manages its OWN independent session!
    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        await generate_pdf(order)
```

---

## 5. Committing Transactions Inside Individual Repositories

### Failure Mechanism
If repository methods call `await session.commit()` internally, you lose the ability to compose atomic multi-step operations.

For example, placing an order requires:
1. `order_repo.create(order)`
2. `inventory_repo.deduct(item_id)`
3. `billing_repo.charge(customer_id)`

If `order_repo.create` commits immediately, and `billing_repo.charge` subsequently fails with an insufficient funds error, the order is already permanently committed to the database! The database is now corrupted with an unpaid, orphaned order.

### Bad Practice
```python
class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, order: Order) -> Order:
        self.session.add(order)
        # BUG: Commits prematurely! Destroys atomic multi-repo transactions!
        await self.session.commit()
        return order
```

### Remediated Practice
```python
class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, order: Order) -> Order:
        self.session.add(order)
        # Flush to DB transaction buffer to populate autoincremented IDs without committing
        await self.session.flush()
        return order

# Transactions are committed ATOMICALLY by the session dependency or Unit of Work!
```

---

## 6. Overlooking `use_cache=True` for Non-Idempotent Providers

### Failure Mechanism
FastAPI defaults to `use_cache=True`. If an endpoint requests a dependency multiple times (e.g. across nested sub-dependencies), FastAPI evaluates the callable **once** and passes the cached result to all injection points.

If your dependency generates non-idempotent values—such as UUID transaction nonces, cryptographic salts, or independent savepoints—caching will silently pass the **same value** to all injection points!

### Bad Practice
```python
def generate_nonce() -> str:
    return str(uuid.uuid4())

# BUG: use_cache=True causes nonce_a and nonce_b to receive the EXACT SAME UUID!
@app.post("/two-phase-commit")
async def two_phase_commit(
    nonce_a: Annotated[str, Depends(generate_nonce)],
    nonce_b: Annotated[str, Depends(generate_nonce)],
):
    assert nonce_a != nonce_b  # FAILS! Both variables are identical!
```

### Remediated Practice
```python
@app.post("/two-phase-commit")
async def two_phase_commit(
    nonce_a: Annotated[str, Depends(generate_nonce, use_cache=False)],
    nonce_b: Annotated[str, Depends(generate_nonce, use_cache=False)],
):
    # Fresh execution guaranteed at each injection site
    assert nonce_a != nonce_b
```

---

## 7. Using `Request.state` as an Untyped Global Grab-Bag

### Failure Mechanism
Developers transitioning from older Flask or Django codebases frequently attach domain objects (current user, active database session, shopping cart) to `request.state.user = ...` inside custom middleware.

**The Architectural Problems**:
- **Zero Static Type Safety**: `request.state` is an untyped dictionary. Mypy and IDEs have zero autocomplete or type verification.
- **Hidden Dependencies**: Route handlers do not declare what they need in their function signature.
- **Untestable**: Testing the route requires constructing mock `Request` objects with dynamic attributes instead of simply passing arguments to functions or using `app.dependency_overrides`.

### Bad Practice
```python
# Untyped, hidden dependency attached in middleware
@app.get("/profile")
async def get_profile(request: Request):
    user = request.state.current_user  # Untyped! Mypy cannot verify.
    return {"user_id": user.id}
```

### Remediated Practice
```python
# Explicit, type-safe, and override-able dependency
CurrentUser = Annotated[User, Depends(get_current_user)]

@app.get("/profile")
async def get_profile(user: CurrentUser):
    # Fully typed with autocompletion and easy unit test mocking
    return {"user_id": user.id}
```

---
*Following these rules ensures zero connection leaks, bulletproof transaction boundaries, and cleanly testable enterprise backends.*
