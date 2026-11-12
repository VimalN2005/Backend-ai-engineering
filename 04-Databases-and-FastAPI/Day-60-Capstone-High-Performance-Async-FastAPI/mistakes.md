# Capstone Antipatterns & Architectural Mistakes: FastAPI, SQLAlchemy & Redis

A production deep-dive into the 7 fatal architectural pitfalls encountered when engineering high-scale asynchronous backend services with **FastAPI**, **SQLAlchemy 2.0 AsyncSession**, **Redis Caching**, and **Celery Task Queues**.

---

## 1. Invoking Blocking Synchronous I/O Inside `async def` Route Handlers

### The Failure Mechanism
FastAPI runs all `async def` endpoints directly on the single-threaded ASGI event loop (`asyncio` / `uvloop`). When an engineer introduces blocking calls—such as `time.sleep()`, the synchronous `requests` library, blocking database drivers (`psycopg2`), or CPU-bound cryptographic operations—the entire thread of the event loop halts. 
During that blocking window, **zero concurrent requests can be polled, socket connections timeout with HTTP 504, and throughput collapses from 5,000 req/sec down to 5 req/sec**.

### Flawed Implementation
```python
# CRITICAL ANTIPATTERN: Blocking the single-threaded event loop
@app.get("/api/v1/external-data")
async def fetch_external_data_antipattern():
    # Blocks the entire ASGI event loop for 2 seconds!
    response = requests.get("https://api.partner.com/telemetry")  # SYNCHRONOUS BLOCKING!
    return response.json()
```

### Production Remediation
Either use non-blocking asynchronous clients (`httpx.AsyncClient`) or offload unavoidable synchronous blocking code to the worker threadpool using `run_in_threadpool`:

```python
import httpx
from fastapi.concurrency import run_in_threadpool

# OPTION A: Non-blocking Async I/O (Preferred)
@app.get("/api/v1/external-data-async")
async def fetch_external_data_production():
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get("https://api.partner.com/telemetry")
        return response.json()

# OPTION B: Offload blocking legacy function to threadpool
@app.get("/api/v1/legacy-compute")
async def legacy_compute_production():
    result = await run_in_threadpool(blocking_cpu_heavy_function, arg1, arg2)
    return {"result": result}
```

---

## 2. Session Leakage & Concurrency Collisions with Shared `AsyncSession`

### The Failure Mechanism
SQLAlchemy 2.0's `AsyncSession` is strictly **non-thread-safe and non-reentrant**. A single `AsyncSession` represents a single transaction context over a single database connection.
If an `AsyncSession` is instantiated at module scope, stored in an application singleton, or shared across concurrent coroutines (e.g. via `asyncio.gather`), simultaneous queries collide on the internal buffer:
`InvalidRequestError: This session is in 'committed' state / cannot initiate new operation while another operation is in progress`.

### Flawed Implementation
```python
# CRITICAL ANTIPATTERN: Shared module-level AsyncSession
global_session = AsyncSession(async_engine)

@app.get("/documents/{doc_id}")
async def get_document_antipattern(doc_id: str):
    # Two concurrent requests using global_session will crash each other!
    return await global_session.execute(select(Document).where(Document.id == doc_id))
```

### Production Remediation
Enforce request-scoped, yield-based dependency injection. Each incoming request gets a private `AsyncSession` that is automatically committed on success or rolled back on failure:

```python
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Production request-scoped AsyncSession generator."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

@app.get("/documents/{doc_id}")
async def get_document_production(doc_id: str, db: AsyncSession = Depends(get_db_session)):
    stmt = select(Document).where(Document.id == doc_id)
    return (await db.execute(stmt)).scalar_one_or_none()
```

---

## 3. Dual-Write Race Conditions on Cache Invalidation (Overwriting vs Deleting)

### The Failure Mechanism
When updating a database record, developers often attempt to update the cache simultaneously:
`await db.commit(); await redis.set(key, updated_data)`.
Under high concurrency, this creates a classic distributed dual-write race condition:
```
Time ──▶
Thread 1 (Write A): Writes DB (Val = A) ──────────────────────────▶ Writes Cache (Val = A) [STALE OVERWRITE!]
Thread 2 (Write B):               Writes DB (Val = B) ──▶ Writes Cache (Val = B)
```
Because network packets interleave unpredictably, Write A overwrites Write B in the cache, leaving the cache permanently out of sync with the database until the key expires.

### Production Remediation
**Always Write-Invalidate (Delete the cache key on mutation, never overwrite)**:

```python
@app.put("/documents/{doc_id}")
async def update_document_production(doc_id: str, update_in: DocUpdateDTO, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    doc.title = update_in.title
    await db.commit()

    # WRITE-INVALIDATION: Invalidate cache key; next read will lazily repopulate
    await redis_client.delete(f"cache:doc:{doc_id}")
    return doc
```

---

## 4. The Post-Commit Race Condition in Celery Task Enqueuing

### The Failure Mechanism
Dispatching an asynchronous Celery task *inside* an uncommitted database transaction:
```python
@app.post("/orders")
async def create_order_antipattern(order_in: OrderDTO, db: AsyncSession = Depends(get_db)):
    order = Order(**order_in.dict())
    db.add(order)
    # DISASTER: Task dispatched BEFORE commit returns!
    process_order_task.delay(order_id=order.id)
    await db.commit()  # What if commit takes 20ms or fails?
```
The message reaches the broker in $< 2\text{ ms}$. A waiting worker immediately executes `SELECT * FROM orders WHERE id=order_id`. Under standard PostgreSQL `READ COMMITTED` isolation, the worker's separate transaction cannot see the uncommitted row! The worker crashes with `RecordNotFound` or `DoesNotExist`.

### Production Remediation
Enforce strict post-commit transaction boundaries. In FastAPI/SQLAlchemy:
```python
@app.post("/orders")
async def create_order_production(order_in: OrderDTO, db: AsyncSession = Depends(get_db)):
    order = Order(**order_in.dict())
    db.add(order)
    await db.commit()  # 1. Guaranteed committed to disk first

    # 2. Only dispatch background task AFTER commit successfully returns
    process_order_task.delay(order_id=order.id)
    return order
```

---

## 5. Naive JWT Validation & Broken OAuth2 Scope Enforcement

### The Failure Mechanism
Common security flaws in token verification:
1. **Missing Expiration Enforcement**: Forgetting to check `exp` claim, allowing revoked/ancient tokens to grant access forever.
2. **Algorithm Confusion Attacks**: Allowing `"none"` algorithm in `jwt.decode()`, letting attackers bypass cryptographic signatures by setting `"alg": "none"` in the JWT header.
3. **Coarse Role Checks Instead of Scopes**: Relying solely on `user.is_admin` instead of fine-grained scopes (`read:documents`, `write:documents`, `analytics:read`), preventing secure third-party API integration and least-privilege service-to-service communication.

### Production Remediation
Always pin algorithms strictly, validate cryptographic HMAC-SHA256 signatures, and enforce scope hierarchy dependencies:

```python
def require_scope(required_scope: str):
    """Enforces fine-grained OAuth2 scope permissions."""
    async def scope_dependency(current_user: User = Depends(get_current_user)) -> User:
        user_scopes = set(s.strip() for s in current_user.scopes.split(","))
        if "admin:all" in user_scopes or required_scope in user_scopes:
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: Insufficient privileges. Required scope: '{required_scope}'"
        )
    return scope_dependency
```

---

## 6. Unbounded Database Queries & Missing Pagination Limits

### The Failure Mechanism
Exposing list endpoints without strict upper bounds:
`GET /api/v1/documents?limit=500000` or `select(Document)`.
When the table scales to 500,000 rows:
- PostgreSQL performs a full table scan and transmits megabytes of row data over the TCP wire.
- SQLAlchemy instantiates 500,000 Python ORM model objects, consuming gigabytes of RAM.
- Pydantic serializes the massive list into JSON, blocking the CPU and triggering the Linux Out-Of-Memory (OOM) killer, taking down the entire API pod.

### Production Remediation
Always clamp maximum limits, use lightweight index-backed keyset or offset-limit pagination, and stream or project only required columns:

```python
@app.get("/api/v1/documents", response_model=List[DocumentResponse])
async def list_documents_production(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),  # Strictly clamped: Max 100 records
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Document).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()
```

---

## 7. Rate Limiter Memory Leaks on High-Cardinality Client Keys

### The Failure Mechanism
When implementing a sliding-window rate limiter via Redis sorted sets (`ZADD` + `ZREMRANGEBYSCORE`), developers track requests per IP address or user ID. 
If an attacker sends millions of requests using spoofed IP addresses, Redis generates millions of unique sorted set keys (`ratelimit:192.168.x.x`). 
If the key lacks an explicit **Time-To-Live (`EXPIRE`)**, these sorted sets persist in Redis RAM indefinitely. Over hours or days, Redis exceeds its `maxmemory` threshold, halting all caching and session operations across the company.

### Production Remediation
Every rate-limiting operation must atomically set a short key TTL matching the sliding window size:

```python
async def enforce_sliding_window(key: str, limit: int, window_sec: int):
    now = time.time()
    window_start = now - window_sec
    r = get_redis()

    # Atomic pipeline: Prune old entries, add current, refresh TTL, count active
    await r.zremrangebyscore(key, 0, window_start)
    await r.zadd(key, {f"{now}_{uuid.uuid4().hex}": now})
    await r.expire(key, window_sec)  # CRITICAL: Automatically garbage-collects key!
    count = await r.zcard(key)

    if count > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
```
